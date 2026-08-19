# -*- coding: utf-8 -*-
"""
Created on Fri May 15 09:31:43 2026

@author: eloua
"""

"""fait varier un paramètre à la fois pour voir leur impact 
sur les biomasses et ensuite choisir correctement les paramètres à calibre"""

import os
import json
import threading, uuid
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from parser_aqt import AquatoxHybridParser, AquatoxRunner
from aqt_output  import read_results

# meme palette que analysis.py
#----------------------------------------------------------------------------------------

STYLE = {
    "fig_bg"  : "#0f1117",
    "ax_bg"   : "#161b22",
    "text"    : "#e6edf3",
    "grid"    : "#21262d",
    "accent"  : "#58a6ff",
    "good"    : "#3fb950",
    "bad"     : "#f85149",
    "cmap"    : "plasma",
}

def _apply_style(fig, axes):
    fig.patch.set_facecolor(STYLE["fig_bg"])
    for ax in (axes if hasattr(axes, '__iter__') else [axes]):
        ax.set_facecolor(STYLE["ax_bg"])
        ax.tick_params(colors=STYLE["text"])
        ax.xaxis.label.set_color(STYLE["text"])
        ax.yaxis.label.set_color(STYLE["text"])
        ax.title.set_color(STYLE["text"])
        for spine in ax.spines.values():
            spine.set_edgecolor(STYLE["grid"])
        ax.grid(color=STYLE["grid"], linewidth=0.6, linestyle='--')

# Bloc principal
#--------------------------------------------------------------------------------------------------

class SensitivityAnalyzer:
    """Pour chaque paramètre marqué OUI dans l'Excel :
        - On part des valeurs actuelles (point de référence = baseline)
        - On fait varier ce paramètre seul sur n_points dans sa plage de tolérance
        - On lance n_points simulations AQUATOX
        - On enregistre la réponse de toutes les biomasses
    Résultat: des courbes paramètre/biomasse + un coeff de  changement tarpin utile"""

    def __init__(self, parser, excel_path, aquatox_exe,
                 output_txt, output_csv, history_path=None):
        self.parser       = parser
        self.excel_path   = excel_path
        self.runner       = AquatoxRunner(aquatox_exe)
        self.output_txt   = output_txt
        self.output_csv   = output_csv
        self.history_path = history_path
        self._params   = []  # liste de dicts de métadonnées
        self._baseline = {}  # valeurs de référence de tous les paramètres OUI
        self.history   = []  # tous les runs
        self._lock = threading.Lock()   # protège self.history et l'écriture JSON en parallèle

    # Chargement
    # ------------------------------------------------------------------

    def load_setup(self):
        """Lit l'Excel et prépare les paramètres et la baseline."""
        df     = pd.read_excel(self.excel_path)
        df_oui = df[df["A CALIBRER (OUI/NON)"].str.upper() == "OUI"].copy()
        if df_oui.empty:
            raise ValueError("No parameter marked OUI in Excel.")

        self._params   = []
        self._baseline = {}
        for _, row in df_oui.iterrows():
            org      = row["Groupe"]
            tech_key = row["Clé technique"]
            val      = float(str(row["Valeur actuelle"]).replace(',', '.'))
            tol_pct  = float(row["Tolérance (%)"]) / 100.0
            
            if tol_pct == 0.0:
                continue   # Tolérance nulle → paramètre ignoré (comme en calibration)
                
            self._params.append({
                "org"     : org,
                "tech_key": tech_key,
                "val"     : val,
                "val_min" : val * (1 - tol_pct),
                "val_max" : val * (1 + tol_pct),
                "tol_pct" : tol_pct,
                "label"   : f"{org}__{tech_key}",
            })
            self._baseline[f"{org}__{tech_key}"] = val

        print(f"Configurable sensitivity: {len(self._params)} parameter(s).")
        for p in self._params:
            print(f"  {p['label']} — [{p['val_min']:.4g}, {p['val_max']:.4g}]")

        return len(self._params)

    # ------------------------------------------------------------------
    # WORKER (appelé en séquentiel ou en parallèle)
    # ------------------------------------------------------------------

    def _run_single_point(self, job, stop_event=None, pause_event=None):
        """Exécute une simulation pour un (paramètre, valeur) donné.
        Utilise des fichiers .txt/.csv isolés par run_id — indispensable
        dès qu'on tourne en parallèle, sinon plusieurs threads s'écrasent
        mutuellement en écrivant sur le même output_txt/output_csv.
        job : dict {"p": param_info, "val": valeur, "sim_count": int, "total": int}
        Retourne l'entry (dict) ou None si le run a été sauté (stop/échec)"""
        p          = job["p"]
        val        = job["val"]
        sim_count  = job["sim_count"]
        total      = job["total"]
        label      = p["label"]

        if pause_event is not None:
            pause_event.wait()

        if stop_event is not None and stop_event.is_set():
            return None

        print(f"  [{sim_count}/{total}] {label} = {val:.4g} ...", end=" ")

        # Chemins isolés pour ce worker
        run_id   = uuid.uuid4().hex[:8]
        base_dir = os.path.dirname(self.output_txt)
        local_txt = os.path.join(base_dir, f"_worker_{run_id}.txt")
        local_csv = os.path.join(base_dir, f"_worker_{run_id}.csv")

        try:
            self._write_single_param(p, val, output_path=local_txt)

            success = self.runner.run_simulation(local_txt, local_csv)
            if not success:
                print("Failed: run ignored.")
                return None

            try:
                results = read_results(local_csv)
            except Exception as e:
                print(f"Error reading: {e}")
                return None
        finally:
            # Nettoyage systématique même en cas d'erreur
            for f in (local_txt, local_csv):
                if os.path.exists(f):
                    os.remove(f)

        entry = {
            "param_varied": label,
            "param_value" : float(val),
        }
        for sp, bm in results.items():
            entry[f"bio__{sp}"] = bm

        org_short = p["org"].split(":")[-1].strip().strip("[]")
        print(f"OK  (bio {org_short} = {results.get(org_short, float('nan')):.4g})")

        # Écriture thread-safe dans l'historique
        with self._lock:
            self.history.append(entry)
            if self.history_path:
                self._save_history()
        return entry

    # Lancement
    # --------------------------------------------------------------------------

    def run(self, n_points=10, n_workers=1, stop_event=None, pause_event=None):
        """Lance l'analyse de sensibilité.
        n_points : nombre de valeurs testées par paramètre (défaut 10)
                   Total simulations = n_params × n_points
        Retourne le DataFrame complet"""
        n_params = len(self._params)
        total    = n_params * n_points

        print(f"\n{'='*60}")
        print(f"  SENSITIVITY ANALYSIS")
        print(f"  {n_params} paramètre(s) × {n_points} points = {total} simulations")
        print(f"  Parallel workers: {n_workers}")
        print(f"{'='*60}\n")

        #Construction de la liste complète des jobs à exécuter
        jobs = []
        sim_count = 0
        for p in self._params:
            values = np.linspace(p["val_min"], p["val_max"], n_points)
            for val in values:
                sim_count += 1
                jobs.append({"p": p, "val": val, "sim_count": sim_count, "total": total})

        if n_workers == 1:
            # séquentiel — comportement identique à avant
            for job in jobs:
                if stop_event is not None and stop_event.is_set():
                    print("Stop sensibility asked.")
                    break
                self._run_single_point(job, stop_event=stop_event, pause_event=pause_event)
        else:
            # parallèle
            with ThreadPoolExecutor(max_workers=n_workers) as executor:
                futures = [
                    executor.submit(self._run_single_point, job,
                                     stop_event=stop_event, pause_event=pause_event)
                    for job in jobs
                ]
                for f in futures:
                    f.result()   # remonte les exceptions éventuelles

        print(f"\n{'='*60}")
        print(f"  Analysis done: {len(self.history)} runs registred.")
        print(f"{'='*60}\n")

        return self.get_history_df()


    # Utilitaires
    # ----------------------------------------------------------------------------

    def _write_single_param(self, param_varied, new_val, output_path=None):
        """Écrit le fichier .txt avec :
          - param_varied à new_val
          - tous les autres paramètres OUI à leur valeur baseline
        output_path : si fourni, écrit dans ce fichier isolé au lieu de
                      self.output_txt (nécessaire pour le parallélisme,
                      chaque worker a son propre fichier)"""
        new_lines = list(self.parser.all_lines)
        path = output_path or self.output_txt
        for p in self._params:
            org      = p["org"]
            tech_key = p["tech_key"]

            val_to_write = float(new_val) if p["label"] == param_varied["label"] \
                           else self._baseline[p["label"]]

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
            new_lines[line_idx] = f'{indent}"{tech_key}":  {val_to_write:.6E}{suffix}\n'

        with open(path, 'w', encoding='latin-1') as f:
            f.writelines(new_lines)

    def get_history_df(self):
        return pd.DataFrame(self.history)

    def _save_history(self):
        with open(self.history_path, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)

# graphes
#-------------------------------------------------------------------------------------------------

def load_sensitivity_history(history_path):
    #Charge l'historique JSON de sensibilité
    with open(history_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    df           = pd.DataFrame(data)
    param_labels = df["param_varied"].unique().tolist()
    bio_cols     = [c for c in df.columns if c.startswith("bio__")]
    print(f"Historique sensibilité : {len(df)} runs, "
          f"{len(param_labels)} paramètre(s), {len(bio_cols)} espèce(s).")
    return df, param_labels, bio_cols


def plot_sensitivity_curves(df, param_label, bio_cols, output_path=None):
    """Pour un paramètre donné, trace biomasse = f(paramètre) pour chaque espèce.
    Toutes choses égales par ailleurs courbes mieux que precedente (pas de multi influences)"""
    df_p = df[df["param_varied"] == param_label].copy().sort_values("param_value")

    if df_p.empty:
        print(f"No run for {param_label}.")
        return

    n_species  = len(bio_cols)
    n_cols     = min(3, n_species)
    n_rows     = max(1, (n_species + n_cols - 1) // n_cols)
    fig, axes = plt.subplots(n_rows, n_cols,figsize=(5 * n_cols, 4 * n_rows), squeeze=False)
    axes_flat = axes.flatten()
    _apply_style(fig, axes_flat)
    x           = df_p["param_value"].values
    param_short = param_label.split('__')[-1]
    org_name    = param_label.split('__')[0]

    for k, bio_col in enumerate(bio_cols):
        ax = axes_flat[k]
        y  = df_p[bio_col].values
        ax.plot(x, y, color=STYLE["accent"], linewidth=1.5,
                marker='o', markersize=4, markerfacecolor=STYLE["good"])

        # Ligne baseline (centre de la plage)
        mid_idx = len(x) // 2
        ax.axvline(x[mid_idx], color=STYLE["text"], linewidth=0.6, linestyle='--', alpha=0.4)
        ax.set_xlabel(f"{param_short} [{org_name}]", fontsize=8)
        ax.set_ylabel("Final biomass", fontsize=8)
        ax.set_title(bio_col.replace("bio__", ""), fontsize=9)

    for k in range(n_species, len(axes_flat)):
        axes_flat[k].set_visible(False)

    fig.suptitle(
        f"Sensibility : {param_short} ({org_name})",
        color=STYLE["text"], fontsize=11
    )
    fig.subplots_adjust(top=0.93, hspace=0.50, wspace=0.38)
    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor=STYLE["fig_bg"])
        print(f"Saved graph : {os.path.basename(output_path)}")
    else:
        plt.show()

    plt.close(fig)

#Heatmap de sensibilité 
#--------------------------------------------------------------------------------------
def plot_sensitivity_heatmap(df, param_labels, bio_cols, output_path=None):
    """Pour chaque case (paramètre P, espèce S) :
        sensibilité = (bio_max - bio_min) / bio_ref × 100  (%)
    avec bio_ref = biomasse au point central de la plage (baseline)"""
    sens_matrix = np.zeros((len(param_labels), len(bio_cols)))
    for i, plabel in enumerate(param_labels):
        df_p    = df[df["param_varied"] == plabel].sort_values("param_value")
        mid_idx = len(df_p) // 2

        for j, bc in enumerate(bio_cols):
            vals    = df_p[bc].values
            bio_ref = vals[mid_idx] if len(vals) > mid_idx else 0.0
            
            if bio_ref == 0 or not np.isfinite(bio_ref):
                sens_matrix[i, j] = 0.0
            
            else:
                sens_matrix[i, j] = (vals.max() - vals.min()) / abs(bio_ref) * 100

    param_labels_short = [f"{p.split('__')[0]}\n{p.split('__')[1]}" for p in param_labels]
    species_labels     = [c.replace('bio__', '') for c in bio_cols]
    fig, ax = plt.subplots(figsize=(max(6, len(bio_cols) * 1.4), max(4, len(param_labels) * 0.9)))
    _apply_style(fig, ax)
    vmax = np.percentile(sens_matrix[sens_matrix > 0], 95) \
           if (sens_matrix > 0).any() else 1.0

    im = ax.imshow(sens_matrix, cmap='YlOrRd', aspect='auto', vmin=0, vmax=vmax)

    for i in range(len(param_labels)):
        for j in range(len(bio_cols)):
            val   = sens_matrix[i, j]
            color = 'white' if val > np.percentile(sens_matrix, 70) else STYLE["text"]
            ax.text(j, i, f"{val:.0f}%", ha='center', va='center',
                    fontsize=7, color=color)

    ax.set_xticks(range(len(bio_cols)))
    ax.set_xticklabels(species_labels, rotation=40, ha='right', fontsize=8)
    ax.set_yticks(range(len(param_labels)))
    ax.set_yticklabels(param_labels_short, fontsize=8)
    ax.set_title("Parametric sensitivity: biomass variation over tolerance range",
                 color=STYLE["text"], fontsize=10)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Biomass variation (%)", color=STYLE["text"])
    cbar.ax.yaxis.set_tick_params(color=STYLE["text"])
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=STYLE["text"])
    cbar.outline.set_edgecolor(STYLE["grid"])
    fig.tight_layout()
    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor=STYLE["fig_bg"])
        print(f"Heatmap sensibility saved : {os.path.basename(output_path)}")
    else:
        plt.show()

    plt.close(fig)

def compute_sensitivity_scores(df, param_labels, bio_cols, threshold=5.0):
    """Pour chaque paramètre, calcule :
      - le score par biomasse : (bio_max - bio_min) / bio_ref × 100 %
      - le score max global (influence maximale sur n'importe quelle biomasse)
      - un flag "low_influence" si score_max < threshold
    Retourne un dict :
      { "org__tech_key": {
          "max_score": float,            # influence max toutes biomasses
          "low_influence": bool,         # True si max_score < threshold
          "scores_by_bio": {             # détail par espèce
              "NomEspece": float, ..."""
    results = {}
    for plabel in param_labels:
        df_p    = df[df["param_varied"] == plabel].sort_values("param_value")
        mid_idx = len(df_p) // 2
        scores_by_bio = {}
        max_score = 0.0
        for bc in bio_cols:
            vals    = df_p[bc].values
            bio_ref = vals[mid_idx] if len(vals) > mid_idx else 0.0
            if bio_ref == 0 or not np.isfinite(bio_ref):
                scores_by_bio[bc.replace("bio__", "")] = 0.0
                continue
            score = (vals.max() - vals.min()) / abs(bio_ref) * 100
            if not np.isfinite(score):
                score = 0.0
            scores_by_bio[bc.replace("bio__", "")] = round(score, 2)
            if score > max_score:
                max_score = score
        results[plabel] = {
            "max_score"     : round(max_score, 2),
            "low_influence" : 1 if max_score < threshold else 0,
            "scores_by_bio" : scores_by_bio,
        }
    return results

# Generation de tout les plots
#-----------------------------------------------------------------------------------------------

def generate_sensitivity_plots(history_path, output_dir):
    """Génère tous les graphes depuis le JSON de sensibilité.
    Peut être appelé sans relancer les simulations si le JSON existe déjà"""
    os.makedirs(output_dir, exist_ok=True)
    df, param_labels, bio_cols = load_sensitivity_history(history_path)

    for plabel in param_labels:
        safe = plabel.replace('__', '_').replace(' ', '_') \
                     .replace(':', '').replace('[', '').replace(']', '')
        plot_sensitivity_curves(df, plabel, bio_cols,
                                output_path=os.path.join(output_dir, f"sens_{safe}.png"))

    plot_sensitivity_heatmap( df, param_labels, bio_cols,
        output_path=os.path.join(output_dir, "sens_heatmap.png"))
    print(f"Sensitivity graphes generated in : {output_dir}")
