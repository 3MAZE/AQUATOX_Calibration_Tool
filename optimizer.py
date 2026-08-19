# -*- coding: utf-8 -*-
"""
Created on Thu May 7 10:42:28 2026

@author: eloua
"""
#Partie optimisation, soit avec CMA-ES soit DE, en auto ou pas

import os
import shutil
import json
import numpy as np
import pandas as pd
import threading, uuid
import cma
import re
from concurrent.futures import ThreadPoolExecutor
from scipy.optimize import minimize, differential_evolution

from parser_aqt import AquatoxHybridParser, update_txt_from_excel, AquatoxRunner
from aqt_output  import read_results, compare_to_initial



# Calcul de l'objectif: RMSE normalisée
#--------------------------------------------------------------------------

def normalized_rmse(results_means, initial_conditions):
    """Pour chaque espèce :
        erreur_i = (V_finale_i - VI_i) / VI_i
    RMSE = sqrt( mean( erreur_i^2 ) )"""
    errors = []
    for species, vi in initial_conditions.items():
        if vi <= 0:
            continue
        # Recherche de la biomasse finale correspondante
        v_finale = _match_species(species, results_means)
        if v_finale is None:
            continue
        errors.append(((v_finale - vi) / vi) ** 2)

    if not errors:
        return float('inf')

    return float(np.sqrt(np.mean(errors)))


def _match_species(species_name, results_means):
    #Même logique que _find_matching_result dans aqt_output.py

    if species_name in results_means:
        return results_means[species_name]
    match = re.search(r'\[([^\]]+)\]', species_name)
    if match:
        short = match.group(1)
        if short in results_means:
            return results_means[short]
    s_low = species_name.lower()
    for key, val in results_means.items():
        if len(key) > 3 and (key.lower() in s_low or s_low in key.lower()):
            return val
    return None


# Optimisateur
#-----------------------------------------------------------------------------

class AquatoxOptimizer:
    """Encapsule la boucle d'optimisation Nelder-Mead sur les paramètres
    marqués OUI dans l'Excel de calibration.

    Attributs publics après optimisation :
        best_params   : dict  clé → valeur optimale
        best_rmse     : float RMSE minimale atteinte
        history       : list  de dicts — toutes les évaluations (pour analysis.py)
        result_scipy  : objet OptimizeResult de scipy"""

    def __init__(self, parser, excel_path, aquatox_exe,
                 output_txt, output_csv, history_path=None):

        self.parser        = parser                     # AquatoxHybridParser déjà scanné
        self.excel_path    = excel_path                 # chemin vers l'Excel
        self.runner        = AquatoxRunner(aquatox_exe) # chemin vers AQUATOX
        self.output_txt    = output_txt                 # fichier .txt temporaire
        self.output_csv    = output_csv                 # fichier CSV de sortie AQUATOX
        self.history_path  = history_path               # (optionnel) chemin JSON pour sauvegarder l'historique

        # Chargé depuis l'Excel
        self._params_to_calibrate = []   # liste de dicts de métadonnées
        self._x0        = np.array([])   # point de départ (valeurs actuelles)
        self._bounds    = []             # [(min, max), ...]
        self._initial_conditions = {}    # cible de calibration

        # Résultats
        self.best_params  = {}
        self.best_rmse    = float('inf')
        self.history      = []           # chaque éval -> dict params + rmse + biomasses
        self.result_scipy = None

        self._iteration   = 0
        
        self._iter_lock = threading.Lock()
        self._stop_event  = None
        self._pause_event = None

    # initialisation/chargement
    # -------------------------------------------------------------------------------

    def load_calibration_setup(self, auto_mode=False):
        """Lit l'Excel et prépare le vecteur de paramètres x0 et les bornes.
        auto_mode=False : prend uniquement les lignes OUI, ignore tolérance 0%
        auto_mode=True  : ignore la colonne OUI/NON, prend tout ce qui a tolérance ≠ 0%
        Retourne le nombre de paramètres à optimiser"""
        df = pd.read_excel(self.excel_path)

        if auto_mode:
            # Mode auto : tout ce qui a une tolérance > 0%, peu importe OUI/NON
            df_sel = df[df["Tolérance (%)"].astype(float) > 0].copy()
            if df_sel.empty:
                raise ValueError("No parameter with tolerance > 0% in the Excel.")
        else:
            # Mode normal : OUI seulement
            df_sel = df[df["A CALIBRER (OUI/NON)"].str.upper() == "OUI"].copy()
            if df_sel.empty:
                raise ValueError("No parameter with OUI in the Excel.")

        self._params_to_calibrate = []
        x0_list     = []
        bounds_list = []
        skipped     = 0

        for _, row in df_sel.iterrows():
            org          = row["Groupe"]
            tech_key     = row["Clé technique"]
            val_actuelle = float(str(row["Valeur actuelle"]).replace(',', '.'))
            tol_pct      = float(row["Tolérance (%)"]) / 100.0

            # Filtre tolérance 0% dans les deux modes
            if tol_pct == 0.0 or val_actuelle == 0.0:
                print(f"  Ignoré ({'tol 0%' if tol_pct==0 else 'valeur=0'}) : {org} / {tech_key}")
                skipped += 1
                continue

            val_min = val_actuelle * (1 - tol_pct)
            val_max = val_actuelle * (1 + tol_pct)
            val_min, val_max = min(val_min, val_max), max(val_min, val_max)

            self._params_to_calibrate.append({
                "org"      : org,
                "tech_key" : tech_key,
                "val_init" : val_actuelle,
                "tol_pct"  : tol_pct,
            })
            x0_list.append(val_actuelle)
            bounds_list.append((val_min, val_max))

        self._x0     = np.array(x0_list)
        self._bounds = bounds_list
        self._initial_conditions = self.parser.get_initial_conditions()

        n = len(self._params_to_calibrate)
        print(f"Optimisation configurée : {n} paramètre(s) à calibrer"
              f"{f' ({skipped} ignoré(s) — tolérance 0%)' if skipped else ''}.")
        for p in self._params_to_calibrate:
            print(f"  {p['org']} / {p['tech_key']} — "
                  f"val={p['val_init']:.4g}, tol=±{p['tol_pct']*100:.0f}%")

        return n

    def load_calibration_setup_from_list(self, params_list):
        """Charge un bloc de paramètres défini manuellement (mode auto par blocs)
        params_list : liste de dicts {"org", "tech_key", "val_init", "tol_pct"}
                      issus de get_all_calibrable_params()"""
        self._params_to_calibrate = []
        x0_list     = []
        bounds_list = []

        for p in params_list:
            val_min = p["val_init"] * (1 - p["tol_pct"])
            val_max = p["val_init"] * (1 + p["tol_pct"])
            val_min, val_max = min(val_min, val_max), max(val_min, val_max)
            self._params_to_calibrate.append(p)
            x0_list.append(p["val_init"])
            bounds_list.append((val_min, val_max))

        self._x0     = np.array(x0_list)
        self._bounds = bounds_list
        self._initial_conditions = self.parser.get_initial_conditions()

        print(f"  Bloc configuré : {len(params_list)} paramètre(s).")
        for p in params_list:
            print(f"    {p['org']} / {p['tech_key']} — "
                  f"val={p['val_init']:.4g}, tol=±{p['tol_pct']*100:.0f}%")

        return len(params_list)

    # Fonction objectif (appelée par scipy)
    # ----------------------------------------------------------------------------

    def _objective(self, x):
        """Évalue la RMSE pour un vecteur de paramètres x.
        Projette x dans les bornes (Nelder-Mead n'impose pas les contraintes)"""
        # self._iteration += 1

        # # Projection dans les bornes (clipping)
        # x_clipped = np.clip(x, [b[0] for b in self._bounds],
        #                        [b[1] for b in self._bounds])

        # # --- Écriture des paramètres dans le .txt ---
        # self._write_params_to_txt(x_clipped)

        # # --- Lancement AQUATOX ---
        # success = self.runner.run_simulation(self.output_txt, self.output_csv)
        # if not success:
        #     print(f"  [iter {self._iteration}] Simulation échouée — pénalité max.")
        #     return 1e6

        # # --- Lecture des résultats ---
        # try:
        #     results_means = read_results(self.output_csv)
        # except Exception as e:
        #     print(f"  [iter {self._iteration}] Erreur lecture CSV : {e}")
        #     return 1e6

                # Pause — attend que l'utilisateur reprenne
        if self._pause_event is not None:
            self._pause_event.wait()

        # Stop définitif
        if self._stop_event is not None and self._stop_event.is_set():
            return 1e6

        with self._iter_lock:          # compteur thread-safe
            self._iteration += 1
            iteration = self._iteration
    
        x_clipped = np.clip(x, [b[0] for b in self._bounds],
                               [b[1] for b in self._bounds])
    
        # Chemins isolés pour ce worker
        run_id   = uuid.uuid4().hex[:8]
        base_dir = os.path.dirname(self.output_txt)
        local_txt = os.path.join(base_dir, f"_worker_{run_id}.txt")
        local_csv = os.path.join(base_dir, f"_worker_{run_id}.csv")
    
        try:
            self._write_params_to_txt(x_clipped, output_path=local_txt)
            success = self.runner.run_simulation(local_txt, local_csv)
            if not success:
                print(f"  [iter {iteration}] simulation failed: max penaltie.")
                return 1e6
            try:
                results_means = read_results(local_csv, txt_path=local_txt)
            except Exception as e:
                print(f"  [iter {iteration}] Error CSV reading : {e}")
                return 1e6
        finally:
            # Nettoyage systématique même en cas d'erreur
            for p in (local_txt, local_csv):
                if os.path.exists(p):
                    os.remove(p)
                    
        # --- Calcul RMSE ---
        rmse = normalized_rmse(results_means, self._initial_conditions)

        # --- Enregistrement dans l'historique ---
        entry = {"iteration": self._iteration, "rmse": rmse}
        for i, p in enumerate(self._params_to_calibrate):
            entry[f"{p['org']}__{p['tech_key']}"] = float(x_clipped[i])
        for sp, bm in results_means.items():
            entry[f"bio__{sp}"] = bm
        self.history.append(entry)
    
        if self.history_path:           # ← plus d'appel à _save_history, on écrit directement
            with open(self.history_path, 'a', encoding='utf-8') as f:
                json.dump(entry, f, ensure_ascii=False)
                f.write('\n')
            
        # Mise à jour du meilleur
        if rmse < self.best_rmse:
            self.best_rmse = rmse
            self.best_params = {
                f"{p['org']}__{p['tech_key']}": float(x_clipped[i])
                for i, p in enumerate(self._params_to_calibrate)
            }
            print(f"  [iter {self._iteration}] New best RMSE = {rmse:.4f}")
        else:
            print(f"  [iter {self._iteration}] RMSE = {rmse:.4f} "
                  f"(best : {self.best_rmse:.4f})")

        return rmse

    # lancement des optimisations
    # ------------------------------------------------------------------

   

    def run(self, max_iter=200, tol=1e-4, n_workers=1, algo='de', stop_event=None, pause_event=None):
        n_params = len(self._params_to_calibrate)
    
        print(f"\n{'='*44}")
        print(f"  ALGORITHM : {algo.upper()}")
        print(f"  {n_params} parameter(s): max {max_iter} generations")
        print(f"  Parallel workers  : {n_workers}")
        print(f"{'='*44}\n")
    
        self._iteration = 0
        self._stop_event  = stop_event
        self._pause_event = pause_event
    
        if algo == 'cma':
            self._run_cma(max_iter, tol, n_workers)
        else:  # 'de' par défaut
            self._run_de(max_iter, tol, n_workers)
    
        print(f"\n{'='*44}")
        print(f"  OPTIMISATION DONE")
        print(f"  final RMSE   : {self.best_rmse:.4f}")
        print(f"  Evaluations  : {self._iteration}")
        print(f"{'='*44}\n")
    
        return self.get_history_df()
    
    
    def _run_de(self, max_iter, tol, n_workers):
        self.result_scipy = differential_evolution(
            self._objective,
            bounds   = self._bounds,
            maxiter  = max_iter,
            tol      = tol,
            seed     = 42,
            polish   = True,
            workers  = n_workers,
            disp     = True,
        )
    
    
    def _run_cma(self, max_iter, tol, n_workers):

        lower  = [b[0] for b in self._bounds]
        upper  = [b[1] for b in self._bounds]
        sigma0 = 0.2
    
        es = cma.CMAEvolutionStrategy(self._x0, sigma0, {
            'bounds'  : [lower, upper],
            'maxiter' : max_iter,
            'tolfun'  : tol,
            'verbose' : 1,
        })
    
        if n_workers == 1:
            # séquentiel
            while not es.stop():
                solutions = es.ask()
                fitnesses = [self._objective(x) for x in solutions]
                es.tell(solutions, fitnesses)
        else:
            # parallèle
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                while not es.stop():
                    solutions = es.ask()
                    fitnesses = list(executor.map(self._objective, solutions))
                    es.tell(solutions, fitnesses)
    
        self.result_scipy = es.result

    # UTILITAIRES
    # ------------------------------------------------------------------

    def _write_params_to_txt(self, x, output_path=None):
        """Écrit les valeurs du vecteur x dans le fichier .txt temporaire
        en utilisant la même mécanique que update_txt_from_excel"""
        new_lines = list(self.parser.all_lines)
        path = output_path or self.output_txt
        for i, p in enumerate(self._params_to_calibrate):
            org      = p["org"]
            tech_key = p["tech_key"]
            new_val  = float(x[i])

            param_info = self.parser.organism_zones[org]["params"].get(tech_key)
            if param_info is None:
                continue

            line_idx = param_info["ligne"]
            old_line = new_lines[line_idx]

            if f'"{tech_key}":' not in old_line:
                continue

            indent    = old_line[: len(old_line) - len(old_line.lstrip())]
            has_comma = old_line.rstrip().endswith(',')
            suffix    = "," if has_comma else ""
            new_lines[line_idx] = f'{indent}"{tech_key}":  {new_val:.6E}{suffix}\n'
           
            
        with open(path, 'w', encoding='latin-1') as f:
            f.writelines(new_lines)

    def get_history_df(self):
        #Retourne l'historique en DataFrame
        return pd.DataFrame(self.history)

    def export_best_params(self, output_path):
        """Exporte les meilleurs paramètres trouvés dans un Excel lisible,
        avec comparaison valeur initiale / valeur optimale"""
        rows = []
        for i, p in enumerate(self._params_to_calibrate):
            key = f"{p['org']}__{p['tech_key']}"
            rows.append({
                "Groupe"          : p["org"],
                "Clé technique"   : p["tech_key"],
                "Valeur initiale" : p["val_init"],
                "Valeur optimale" : round(self.best_params.get(key, float('nan')), 6),
                "Variation (%)"   : round(
                    (self.best_params.get(key, p["val_init"]) - p["val_init"])
                    / p["val_init"] * 100, 1
                ) if p["val_init"] != 0 else float('nan'),
            })

        df = pd.DataFrame(rows)
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name="Params_optimaux")
            ws = writer.sheets["Params_optimaux"]
            for col_idx, width in enumerate([28, 18, 18, 18, 14], start=1):
                ws.column_dimensions[
                    ws.cell(row=1, column=col_idx).column_letter
                ].width = width

        print(f"Optimised parameters: {os.path.basename(output_path)}")


# mode auto (optimisation complète par blocs)
#---------------------------------------------------------------------------------------

def get_all_calibrable_params(excel_path):
    """Lit l'Excel et retourne tous les paramètres avec tolérance > 0%,
    dans l'ordre Excel, peu importe la colonne OUI/NON.
    Retourne une liste de dicts :
        {"org", "tech_key", "val_init", "tol_pct"}"""
    df     = pd.read_excel(excel_path)
    df_sel = df[(df["Tolérance (%)"].astype(float) > 0) & (df["Valeur actuelle"] != 0.0)].copy()

    params = []
    for _, row in df_sel.iterrows():
        params.append({
            "org"      : row["Groupe"],
            "tech_key" : row["Clé technique"],
            "val_init" : float(str(row["Valeur actuelle"]).replace(',', '.')),
            "tol_pct"  : float(row["Tolérance (%)"]) / 100.0,
        })
    return params


def get_calibrable_params_oui(excel_path):
    """Lit l'Excel et retourne les paramètres marqués OUI (mode simple/manuel),
    avec le même filtrage (tolérance 0% ou valeur 0 ignorées) que
    AquatoxOptimizer.load_calibration_setup(auto_mode=False).
    Sert à faire bénéficier le mode simple du découpage par blocs, sur la
    même liste de dicts {"org", "tech_key", "val_init", "tol_pct"} que
    get_all_calibrable_params (mode auto)"""
    df     = pd.read_excel(excel_path)
    df_sel = df[df["A CALIBRER (OUI/NON)"].str.upper() == "OUI"].copy()
    if df_sel.empty:
        raise ValueError("No parameters with OUI in the Excel.")

    params  = []
    skipped = 0
    for _, row in df_sel.iterrows():
        val_actuelle = float(str(row["Valeur actuelle"]).replace(',', '.'))
        tol_pct      = float(row["Tolérance (%)"]) / 100.0

        if tol_pct == 0.0 or val_actuelle == 0.0:
            skipped += 1
            continue

        params.append({
            "org"      : row["Groupe"],
            "tech_key" : row["Clé technique"],
            "val_init" : val_actuelle,
            "tol_pct"  : tol_pct,
        })

    if skipped:
        print(f"  {skipped} parameter(s) OUI ignored (tolérance 0% ou valeur=0).")

    return params


def export_best_params_dict(params_list, best_overall, output_path):
    """Équivalent de AquatoxOptimizer.export_best_params, mais à partir d'une
    liste de paramètres {"org","tech_key","val_init"} et du dict
    best_overall (label -> valeur optimale) renvoyé par
    run_calibration_by_blocks / run_full_auto — utile quand la calibration
    tourne bloc par bloc et qu'il n'y a pas une seule instance
    AquatoxOptimizer à interroger à la fin"""
    rows = []
    for p in params_list:
        key     = f"{p['org']}__{p['tech_key']}"
        val_opt = best_overall.get(key, p["val_init"])
        rows.append({
            "Groupe"          : p["org"],
            "Clé technique"   : p["tech_key"],
            "Valeur initiale" : p["val_init"],
            "Valeur optimale" : round(val_opt, 6),
            "Variation (%)"   : round(
                (val_opt - p["val_init"]) / p["val_init"] * 100, 1
            ) if p["val_init"] != 0 else float('nan'),
        })

    df = pd.DataFrame(rows)
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Params_optimaux")
        ws = writer.sheets["Params_optimaux"]
        for col_idx, width in enumerate([28, 18, 18, 18, 14], start=1):
            ws.column_dimensions[
                ws.cell(row=1, column=col_idx).column_letter
            ].width = width

    print(f"Optimised parameters exported : {os.path.basename(output_path)}")


def _commit_values_to_parser(parser, values):
    """Reporte des valeurs optimisées (label -> valeur) directement dans
    parser.all_lines, en mémoire, avec la même mécanique que
    _write_params_to_txt / update_txt_from_excel.
    Sert à ce que le bloc suivant d'une calibration auto reparte d'une
    base à jour, au lieu de repartir du fichier .txt d'origine"""
    for label, new_val in values.items():
        org, tech_key = label.split("__", 1)
        zone = parser.organism_zones.get(org)
        if zone is None:
            continue
        param_info = zone["params"].get(tech_key)
        if param_info is None:
            continue

        line_idx = param_info["ligne"]
        old_line = parser.all_lines[line_idx]
        if f'"{tech_key}":' not in old_line:
            continue

        indent    = old_line[: len(old_line) - len(old_line.lstrip())]
        has_comma = old_line.rstrip().endswith(',')
        suffix    = "," if has_comma else ""
        parser.all_lines[line_idx] = f'{indent}"{tech_key}":  {new_val:.6E}{suffix}\n'


def run_calibration_by_blocks(parser, excel_path, aquatox_exe, output_txt, output_csv,
                              history_path, params_list, block_size, max_iter_per_block,
                              n_workers=1, algo='cma', stop_event=None, pause_event=None):
    """Version générique du découpage par blocs séquentiels : découpe
    `params_list` (déjà filtrée en amont — mode auto = tolérance > 0%,
    mode simple = paramètres cochés OUI) en blocs de `block_size`,
    optimise chaque bloc, et passe les valeurs optimales comme baseline
    au bloc suivant.
    Les résultats de tous les blocs sont agrégés dans un seul historique JSON.
    Retourne un dict : label → valeur optimale finale (tous blocs confondus)"""
    all_params = params_list
    n_total    = len(all_params)
    n_blocs    = (n_total + block_size - 1) // block_size  # arrondi supérieur

    print(f"\n{'='*44}")
    print(f"  CALIBRATION BY BLOCS")
    print(f"  {n_total} paramètre(s) -> {n_blocs} bloc(s) de {block_size}")
    print(f"{'='*44}\n")

    # Valeurs courantes — mises à jour après chaque bloc
    current_values = {
        f"{p['org']}__{p['tech_key']}": p["val_init"]
        for p in all_params
    }

    all_history  = []   # historique agrégé de tous les blocs
    best_overall = {}   # meilleurs params finaux

    for bloc_idx in range(n_blocs):
        if pause_event is not None:
            pause_event.wait()

        # Stop entre blocs
        if stop_event is not None and stop_event.is_set():
            print(f" Stop: {bloc_idx}/{n_blocs} blocs finished.")
            break
        bloc_params = all_params[bloc_idx * block_size : (bloc_idx + 1) * block_size]

        print(f"\n{'─'*44}")
        print(f"  BLOC {bloc_idx + 1}/{n_blocs} — "
              f"{len(bloc_params)} parameter(s)")
        print(f"{'─'*44}")

        # Mise à jour des val_init avec les valeurs optimales des blocs précédents
        for p in bloc_params:
            label = f"{p['org']}__{p['tech_key']}"
            p["val_init"] = current_values[label]

        # Chemin JSON spécifique à ce bloc
        bloc_history_path = history_path.replace('.json', f'_bloc{bloc_idx+1}.json')

        optimizer = AquatoxOptimizer(
            parser       = parser,
            excel_path   = excel_path,
            aquatox_exe  = aquatox_exe,
            output_txt   = output_txt,
            output_csv   = output_csv,
            history_path = bloc_history_path,
        )
        optimizer.load_calibration_setup_from_list(bloc_params)
        optimizer.run(max_iter=max_iter_per_block, n_workers=n_workers, algo=algo,stop_event=stop_event, pause_event=pause_event)

        # Mise à jour des valeurs courantes avec les optimaux de ce bloc
        for label, val in optimizer.best_params.items():
            if label in current_values:
                current_values[label] = val
                best_overall[label]   = val

        # Persiste ces valeurs dans le parser (en mémoire) pour que le
        # PROCHAIN bloc reparte d'une base à jour au lieu du fichier .txt
        # d'origine. Sans ça, les paramètres déjà calibrés reviennent à
        # leur valeur initiale pendant les blocs suivants.
        if optimizer.best_params:
            _commit_values_to_parser(parser, optimizer.best_params)

        # Agrégation de l'historique (avec numéro de bloc pour traçabilité)
        for entry in optimizer.history:
            entry["bloc"] = bloc_idx + 1
        all_history.extend(optimizer.history)

    # Réécrit le fichier .txt de référence avec l'état final (tous blocs
    # confondus), pour qu'il reste cohérent avec parser.all_lines et
    # best_overall à la fin de l'optimisation.
    with open(output_txt, 'w', encoding='latin-1') as f:
        f.writelines(parser.all_lines)

    # Sauvegarde de l'historique agrégé complet
    with open(history_path, 'w', encoding='utf-8') as f:
        json.dump(all_history, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*44}")
    print(f"  COMPLET OPTIMISATION DONE")
    print(f"  {n_blocs} blocs, {len(all_history)} total simulations.")
    print(f"  Global History : {os.path.basename(history_path)}")
    print(f"{'='*44}\n")

    return best_overall


def run_full_auto(parser, excel_path, aquatox_exe, output_txt, output_csv,
                  history_path, block_size=25, max_iter_per_block=25, n_workers=1, algo='cma',
                  stop_event=None, pause_event=None):
    """Mode auto : découpe TOUS les paramètres calibrables (tolérance > 0%,
    peu importe la case OUI/NON) en blocs, via run_calibration_by_blocks"""
    all_params = get_all_calibrable_params(excel_path)
    return run_calibration_by_blocks(
        parser=parser, excel_path=excel_path, aquatox_exe=aquatox_exe,
        output_txt=output_txt, output_csv=output_csv, history_path=history_path,
        params_list=all_params, block_size=block_size,
        max_iter_per_block=max_iter_per_block, n_workers=n_workers, algo=algo,
        stop_event=stop_event, pause_event=pause_event,
    )
