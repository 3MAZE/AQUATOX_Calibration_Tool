# -*- coding: utf-8 -*-

"""
Created on Fri Jun 12 11:44:22 2026

@author: eloua
"""

"""api.py fait le pont frontend PyWebView / backend AQUATOX
Toutes les méthodes de cette classe sont appelables depuis le JS
via window.pywebview.api.nomMethode(...)"""

import os
import shutil
import threading
import contextlib
import io
import json
import webview
import pandas as pd
import psutil

from parser_aqt   import AquatoxHybridParser, ANIMAL_PARAMS, PLANT_PARAMS, DEFAULT_TOLERANCE, \
                         BOOL_DEPENDENCIES, ZERO_MEANS_DISABLED, \
                         generate_excel_config, update_txt_from_excel, AquatoxRunner
from aqt_output   import read_results,read_final_values, compare_to_initial, export_comparison
from optimizer    import run_full_auto, get_all_calibrable_params, \
                         run_calibration_by_blocks, get_calibrable_params_oui, \
                         export_best_params_dict
from analysis     import generate_all_plots
from sensitivity  import SensitivityAnalyzer, generate_sensitivity_plots,compute_sensitivity_scores

def _detect_max_workers(parser, log_fn=None):
    """Estime le nombre de workers parallèles selon les ressources de l'ordinateur.
    Basé sur de l'empirique à affiner: 
    200 Mo RAM pour 32 organismes et fichier de 50Mo"""
    # Infos machine
    physical_cores = psutil.cpu_count(logical=False) or 1
    available_mb   = psutil.virtual_memory().available / 1e6

    if log_fn:
        log_fn(f"  Physical CPU: {physical_cores}")
        log_fn(f"  Available RAM: {available_mb:.0f} MB")

    # Estimation RAM par instance AQUATOX 
    RAM_REF_MB      = 200
    N_ORG_REF       = 32
    SAFETY_FACTOR   = 1.5   # marge de sécurité 50%
    BASE_MB         = 50    # overhead fixe de l'exécutable
    n_organismes = len(parser.organism_zones)
    peak_mb = BASE_MB + (RAM_REF_MB - BASE_MB) * (n_organismes / N_ORG_REF)
    peak_mb_safe = peak_mb * SAFETY_FACTOR
    APP_OVERHEAD_MB = 300   # Python + pywebview + all_lines + pandas
    effective_available = max(0, available_mb - APP_OVERHEAD_MB)
    if log_fn:
        log_fn(f"  Organisms in model: {n_organismes}")
        log_fn(f"  estimated RAM: {peak_mb:.0f} MB (×{SAFETY_FACTOR} -> {peak_mb_safe:.0f} MB)")

    # Nb workers
    max_by_ram = max(1, int((effective_available * 0.8) / peak_mb_safe))
    max_by_cpu = physical_cores
    n_workers = max(1, min(max_by_ram, max_by_cpu))

    if log_fn:
        log_fn(f"  RAM limit: {max_by_ram} workers")
        log_fn(f"  CPU limit: {max_by_cpu} workers")
        log_fn(f"  -> {n_workers} worker(s) selected")

    return n_workers

class _LiveLogger:
    #Redirige stdout vers _log() ligne par ligne en temps réel
    def __init__(self, log_fn):
        self._log = log_fn
        self._buf = ""
    def write(self, s):
        self._buf += s
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            if line.strip():
                self._log(line)
    def flush(self): pass

class AquatoxAPI:
    """Exposé à JS via js_api=AquatoxAPI().
    formatage de la data pour JS en dic {"ok": bool, "data": ..., "error": str}.
    Les simulations/calibrations/etc... tournent dans un thread
    séparé (pour pas freeze) et streament les logs via window.pywebview.api._push_log()"""

    def __init__(self):
        self._window    = None   #viens de main après la création
        self._parser    = None
        self._txt_path  = None
        self._running   = False
        self._last_best = {}
        self._last_params_before = {} 
        self._last_sensitivity = {}
        self._pending_calib_done       = False
        self._pending_sensitivity_done = False
        self._pending_bilan_json = None
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        
    # Outils Python/JS
    # -------------------------------------------------------------------------

    def _ok(self, data=None):
        return {"ok": True,  "data": data,  "error": ""}

    def _err(self, msg):
        return {"ok": False, "data": None,  "error": str(msg)}

    def _log(self, msg):
        #Envoie une ligne de log au frontend via JS
        if self._window:
            safe = msg.replace("\\", "\\\\").replace("`", "\\`").replace("\n", "\\n")
            self._window.evaluate_js(f'window.appendLog(`{safe}`)')  
    
    def _run_in_thread(self, fn, *args):
        #Lance fn(*args) dans un thread, streame stdout -> log en temps réel
        def wrapper():
            self._running = True
            self._pending_calib_done       = False
            self._pending_sensitivity_done = False
            self._stop_event.clear()
            self._pause_event.set()
            try:
                with contextlib.redirect_stdout(_LiveLogger(self._log)):
                    fn(*args)
            except Exception as e:
                self._log(f"❌ Erreur : {e}")
            finally:
                calib_done       = getattr(self, '_pending_calib_done', False)
                sensitivity_done = getattr(self, '_pending_sensitivity_done', False)
                bilan_json = getattr(self, '_pending_bilan_json', None)
                self._running = False
                if self._window:
                    self._window.evaluate_js('window.onTaskDone()')
                    if calib_done:
                        self._pending_calib_done = False
                        self._window.evaluate_js("window.onCalibrationDone()")
                    elif sensitivity_done:
                        self._pending_sensitivity_done = False
                        self._window.evaluate_js("window.onSensitivityDone()")
                    elif bilan_json:
                        self._pending_bilan_json = None
                        self._window.evaluate_js(f'window.onBilanReady({bilan_json})')
        threading.Thread(target=wrapper, daemon=True).start()
            
    def _get_paths(self, exe_path, work_dir):
        if not exe_path or not os.path.exists(exe_path):
            raise ValueError(f"AQUATOX.exe not found : {exe_path}")
        if not work_dir:
            raise ValueError("Working files not selected")
        os.makedirs(work_dir, exist_ok=True)
        return exe_path, work_dir


    # Dialogues entre les fichiers (appellés depuis JS, ouvre une vraie boîte native)
    # ------------------------------------------------------------------

    def choisir_fichier_txt(self):
        """Ouvre un sélecteur de fichier .txt et retourne le chemin."""
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Fichiers AQUATOX (*.txt)", "Tous les fichiers (*.*)")
        )
        if result and len(result) > 0:
            return self._ok(result[0])
        return self._ok(None)

    def choisir_exe(self):
        result = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Exécutable (*.exe)", "Tous les fichiers (*.*)")
        )
        if result and len(result) > 0:
            return self._ok(result[0])
        return self._ok(None)

    def choisir_dossier(self):
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if result and len(result) > 0:
            return self._ok(result[0])
        return self._ok(None)

    
    # Extraction data
    # ------------------------------------------------------------------

    def extraire_donnees(self, txt_path):
        #Parse le fichier AQUATOX et retourne la liste des espèces + paramètres
        try:
            self._parser   = AquatoxHybridParser(txt_path)
            self._txt_path = txt_path
            organisms      = self._parser.scan_file()

            # Construit la structure pour l'UI
            species_data = []
            for org in organisms:
                zone       = self._parser.organism_zones[org]
                org_type   = zone["params"].get("__type", "animal")
                plant_type = zone["params"].get("__plant_type", None)

                # Choisit le bon dict selon le type d'organisme
                if org_type == "plant":
                    param_dict = PLANT_PARAMS.get(plant_type, PLANT_PARAMS["__default__"])
                else:
                    param_dict = ANIMAL_PARAMS

                # Appliquer les pParamètres désactivés 
                disabled = set()
                for bool_key, deps in BOOL_DEPENDENCIES.items():
                    flag = zone["params"].get(f"__bool_{bool_key}", True)
                    if flag is False:
                        disabled.update(deps)

                params = []
                for tech_key, info in zone["params"].items():
                    if tech_key.startswith("__"):        # clés internes
                        continue
                    if tech_key in disabled:             # désactivé par booléen
                        continue
                    if tech_key in ZERO_MEANS_DISABLED and info["valeur"] == 0.0:
                        continue
                    params.append({
                        "tech_key"  : tech_key,
                        "label"     : param_dict.get(tech_key, tech_key),
                        "valeur"    : info["valeur"],
                        "source"    : info["source"],
                        "tolerance" : int(DEFAULT_TOLERANCE.get(tech_key, 0.20) * 100),
                        "calibrer"  : tech_key != "InitialCond" and info["valeur"] != 0.0,
                        "type"      : org_type,
                    })
                species_data.append({"nom": org, "params": params, "type": org_type})
            return self._ok(species_data)
        except Exception as e:
            return self._err(e)
   
    # Les excels
    # ----------------------------------------------------------------------

    def generer_excel(self, work_dir):
        #Génère calibration_setup.xlsx 
        if not self._parser:
            return self._err("Extract data first")
        try:
            os.makedirs(work_dir, exist_ok=True)
            excel_path = os.path.join(work_dir, "calibration_setup.xlsx")
            generate_excel_config(self._parser, excel_path)
            return self._ok(excel_path)
        except Exception as e:
            return self._err(e)

    # run simulation
    # -------------------------------------------------------------------------

    def lancer_simulation_base(self, exe_path, work_dir, params_selectionnes):
        """params_selectionnes = liste de {org, tech_key, valeur, tolerance, calibrer} qui voent de l'excel
        Applique les valeurs de l'UI -> txt temporaire -> lance AQUATOX -> retourne bilan"""
        if not self._parser:
            return self._err("Extract data first")
        if self._running:
            return self._err("Something is already running")

        def _impl():
            exe, wdir   = self._get_paths(exe_path, work_dir)
            excel_path  = os.path.join(wdir, "calibration_setup.xlsx")
            output_txt  = os.path.join(wdir, "simulation_temp.txt")
            output_csv  = os.path.join(wdir, "resultats_simu.csv")
            bilan_xlsx  = os.path.join(wdir, "bilan_calibration.xlsx")

            # Générer l'Excel depuis les valeurs de l'interface
            self._log("Generation of the calibration Excel file…")
            self._appliquer_params_ui(params_selectionnes, excel_path)

            # Copier le fichier source
            shutil.copy2(self._txt_path, output_txt)
            update_txt_from_excel(self._parser, excel_path, output_txt)

            # et la simu
            self._log("Launching the basic simulation…")
            runner  = AquatoxRunner(exe)
            success = runner.run_simulation(output_txt, output_csv)
            if not success:
                self._log("❌ Basic simulation failed")
                return

            results_finals = read_final_values(output_csv, txt_path=output_txt)
            ic             = self._parser.get_initial_conditions()
            df_bilan       = compare_to_initial(results_finals, ic)
            export_comparison(df_bilan, bilan_xlsx)
            self._log(f"✅ Simulation done. Report: {os.path.basename(bilan_xlsx)}")
            self._pending_bilan_json = df_bilan.to_json(orient="records", force_ascii=False)
            
        self._run_in_thread(_impl)
        return self._ok("Simulation launcehd in background")

    # calibration
    # ---------------------------------------------------------------------------

    def lancer_calibration(self, exe_path, work_dir, params_selectionnes, max_iter=25, algo='de', block_size=25):
        if not self._parser:
            return self._err("Extract data first")
        if self._running:
            return self._err("Something is already running")

        def _impl():
            exe, wdir    = self._get_paths(exe_path, work_dir)
            excel_path   = os.path.join(wdir, "calibration_setup.xlsx")
            output_txt   = os.path.join(wdir, "simulation_temp.txt")
            output_csv   = os.path.join(wdir, "resultats_simu.csv")
            history_path = os.path.join(wdir, "optim_history.json")
            best_params  = os.path.join(wdir, "best_params.xlsx")
            self._log(f"Calibration launched — max_iter={max_iter}, blocks={block_size}")
            self._appliquer_params_ui(params_selectionnes, excel_path)
            shutil.copy2(self._txt_path, output_txt)
            update_txt_from_excel(self._parser, excel_path, output_txt)
            self._log("Detecting computer ressources...")
            n_workers = _detect_max_workers(self._parser, log_fn=self._log)

            params_list = get_calibrable_params_oui(excel_path)
            if not params_list:
                self._log("❌ No parameter checked for calibration")
                return

            self._log(f"  {len(params_list)} parameter(s) to optimise…")
            self._last_params_before = {
                f"{p['org']}__{p['tech_key']}": p['val_init']
                for p in params_list
            }

            best = run_calibration_by_blocks(
                parser             = self._parser,
                excel_path         = excel_path,
                aquatox_exe        = exe,
                output_txt         = output_txt,
                output_csv         = output_csv,
                history_path       = history_path,
                params_list        = params_list,
                block_size         = block_size,
                max_iter_per_block = max_iter,
                n_workers          = n_workers,
                algo               = algo,
                stop_event         = self._stop_event,
                pause_event        = self._pause_event,
            )
            export_best_params_dict(params_list, best, best_params)
            self._log("✅ Calibration done")
            # Stocke les résultats pour le pop up de fin
            self._last_best = best

            # mesxage de la review dans le log
            self._pending_calib_done = True

        self._run_in_thread(_impl)
        return self._ok("Calibration launched in background")

    def lancer_calibration_auto(self, exe_path, work_dir, params_selectionnes,
                                max_iter=25, block_size=25,algo='de'):
        if not self._parser:
            return self._err("Extract data first")
        if self._running:
            return self._err("Something is already running")

        def _impl():
            exe, wdir    = self._get_paths(exe_path, work_dir)
            excel_path   = os.path.join(wdir, "calibration_setup.xlsx")
            output_txt   = os.path.join(wdir, "simulation_temp.txt")
            output_csv   = os.path.join(wdir, "resultats_simu.csv")
            history_path = os.path.join(wdir, "optim_history.json")
            self._log(f"Calibration auto — blocs={block_size}, iter/bloc={max_iter}")
            self._appliquer_params_ui(params_selectionnes, excel_path)
            shutil.copy2(self._txt_path, output_txt)
            update_txt_from_excel(self._parser, excel_path, output_txt)
            self._log("Détection des ressources machine...")
            n_workers = _detect_max_workers(self._parser, log_fn=self._log)
            
            """ Valeurs de référence AVANT calibration. Le mode auto  ne sélectionne
             que les paramètres par tolérance > 0% du coup faut reconstruire
            _last_params_before en prenant ça en compte
             sinon get_calibration_results() ne trouve pas de correspondance
             et le pop up de fin reste vide"""
            all_calibrable = get_all_calibrable_params(excel_path)
            self._last_params_before = {
                f"{p['org']}__{p['tech_key']}": p['val_init']
                for p in all_calibrable
            }
            best = run_full_auto(
                parser            = self._parser,
                excel_path        = excel_path,
                aquatox_exe       = exe,
                output_txt        = output_txt,
                output_csv        = output_csv,
                history_path      = history_path,
                block_size        = block_size,
                max_iter_per_block= max_iter,
                n_workers=n_workers,
                stop_event        = self._stop_event,
                pause_event       = self._pause_event,
            )
            self._log("✅ Auto calibration done")
            # Stocke les résultats pour la modale
            self._last_best = best
            # (self._last_params_before a déjà été construit plus haut,
            #  cohérent avec la sélection réelle du mode auto)
            # Propose la revue dans le log
            self._pending_calib_done = True            
        self._run_in_thread(_impl)
        return self._ok("Auto calibration launched in background")


    # sensibilité
    # ------------------------------------------------------------------

    def lancer_sensibilite(self, exe_path, work_dir, params_selectionnes, n_points=5):
        if not self._parser:
            return self._err("Extract data first")
        if self._running:
            return self._err("Something is already running")

        def _impl():
            exe, wdir   = self._get_paths(exe_path, work_dir)
            excel_path  = os.path.join(wdir, "calibration_setup.xlsx")
            output_txt  = os.path.join(wdir, "simulation_temp.txt")
            output_csv  = os.path.join(wdir, "resultats_simu.csv")
            sens_json   = os.path.join(wdir, "sensitivity_history.json")
            sens_dir    = os.path.join(wdir, "graphes_sensibilite")
            self._log(f"Sensibility analysis — {n_points} points/paramètre")
            self._appliquer_params_ui(params_selectionnes, excel_path)
            shutil.copy2(self._txt_path, output_txt)
            self._log("Détection des ressources machine...")
            n_workers = _detect_max_workers(self._parser, log_fn=self._log)
            analyzer = SensitivityAnalyzer(
                parser       = self._parser,
                excel_path   = excel_path,
                aquatox_exe  = exe,
                output_txt   = output_txt,
                output_csv   = output_csv,
                history_path = sens_json,
            )
            analyzer.load_setup()
            analyzer.run(n_points=n_points,stop_event=self._stop_event,n_workers=n_workers,
                         pause_event=self._pause_event)
            generate_sensitivity_plots(sens_json, sens_dir)
            # Calcul des scores de sensibilité
            df_sens     = analyzer.get_history_df()
            bio_cols    = [c for c in df_sens.columns if c.startswith("bio__")]
            param_labels = [p["label"] for p in analyzer._params]
            self._last_sensitivity = compute_sensitivity_scores(df_sens, param_labels, bio_cols)
            self._log(f"✅ Sensibility done. Graphes : {sens_dir}")
            self._pending_sensitivity_done = True

        self._run_in_thread(_impl)
        return self._ok("Sensibility launched in background")

    # graphes
    # ------------------------------------------------------------------

    def generer_graphes(self, work_dir):
        if not self._parser:
            return self._err("Extract data first")
        if self._running:
            return self._err("Something is already running")

        def _impl():
            history_path = os.path.join(work_dir, "optim_history.json")
            plots_dir    = os.path.join(work_dir, "graphes")
            if not os.path.exists(history_path):
                self._log("❌ No historical JSON — Start by launching a calibration")
                return

            ic = self._parser.get_initial_conditions() if self._parser else {}
            generate_all_plots(history_path, ic, plots_dir)
            self._log(f"✅ Graphes generated : {plots_dir}")

        self._run_in_thread(_impl)
        return self._ok("graphes generation launched")

    def sauvegarder_txt_optimise(self, txt_path, work_dir, params_selectionnes):
        """Applique les paramètres actuels de l'UI dans une copie du .txt source
        et sauvegarde dans un 'simulation_calibree.txt'"""

        if not self._parser:
            return self._err("Extract data first")
        try:
            os.makedirs(work_dir, exist_ok=True)
            excel_path  = os.path.join(work_dir, "calibration_setup.xlsx")
            output_txt  = os.path.join(work_dir, "simulation_calibree.txt")

            self._appliquer_params_ui(params_selectionnes, excel_path)
            shutil.copy2(self._txt_path, output_txt)
            update_txt_from_excel(self._parser, excel_path, output_txt)

            return self._ok(output_txt)
        except Exception as e:
            return self._err(e)

    def ouvrir_dossier(self, path):
        #Ouvre l'explorateur Windows sur le dossier donné
        import subprocess, sys
        if not os.path.isdir(path):
            return self._err(f"File not found : {path}")
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return self._ok()

    # applique les valeurs de l'UI dans l'Excel
    # ------------------------------------------------------------------

    def _appliquer_params_ui(self, params_selectionnes, excel_path):
        """params_selectionnes = liste de dicts qui vienne de l'UI puis
        Génère l'Excel avec les valeurs et flags OUI/NON de l'UI"""
        # D'abord génère la base depuis le parser
        generate_excel_config(self._parser, excel_path)
        # Puis on adapte les colonnes avec les choix de l'UI
        df = pd.read_excel(excel_path)
        param_map = {}
        for p in params_selectionnes:
            param_map[(p["org"], p["tech_key"])] = p

        for i, row in df.iterrows():
            key = (row["Groupe"], row["Clé technique"])
            if key in param_map:
                p = param_map[key]
                df.at[i, "Valeur actuelle"]      = p["valeur"]
                df.at[i, "Tolérance (%)"]         = p["tolerance"]
                df.at[i, "A CALIBRER (OUI/NON)"] = "OUI" if p.get("calibrer") else "NON"

        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Calibration")
            
    def get_calibration_results(self):
        """Retourne ancien/nouveau pour la modale de revue"""
        rows = []
        for label, new_val in self._last_best.items():
            old_val = self._last_params_before.get(label)
            if old_val is None:
                continue
            org, tech_key = label.split("__", 1)
            delta = ((new_val - old_val) / old_val * 100) if old_val != 0 else 0
            rows.append({
                "label" : label,
                "org"   : org,
                "tech_key": tech_key,
                "old"   : old_val,
                "new"   : new_val,
                "delta" : delta,
            })
        return self._ok(rows)
    
    def get_sensitivity_scores(self):
        if not hasattr(self, '_last_sensitivity') or not self._last_sensitivity:
            return self._err("No sensibility analysis available")
        return self._ok(self._last_sensitivity)

    # definition des etats des thread
    # -----------------------------------------------------------------------------

    def est_occupe(self):
        return self._ok(self._running)

    def get_fichier_courant(self):
        return self._ok(self._txt_path)
    
    def stopper(self):
        #Arrêt définitif: pénalité max à la prochaine évaluation
        if not self._running:
            return self._err("Nothing Running")
        self._pause_event.set()   # débloquer si en pause, pour que le stop soit pris en compte
        self._stop_event.set()
        self._log("Stop: finsh running simulation…")
        return self._ok()

    def basculer_pause(self):
        """Pause / reprise: alterne entre les deux états"""
        if not self._running:
            return self._err("Nothing running")
        if self._pause_event.is_set():
            self._pause_event.clear()
            self._log(" waiting: waiting the end of the on going simulation …")
            return self._ok("paused")
        else:
            self._pause_event.set()
            self._log(" Restart")
            return self._ok("running")
