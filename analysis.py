# -*- coding: utf-8 -*-
"""
Created on Fri May 8 14:04:35 2026

@author: eloua
"""
#Partie du programme qui lit les Historiques JSON pour faire des grpahes
import re
import os
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable



# Style/theme
#----------------------------------------------------------------

STYLE = {
    "fig_bg"     : "#0f1117",
    "ax_bg"      : "#161b22",
    "text"       : "#e6edf3",
    "grid"       : "#21262d",
    "accent"     : "#58a6ff",
    "good"       : "#3fb950",
    "bad"        : "#f85149",
    "cmap"       : "plasma",
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

# Chargement historique
#---------------------------------------------------------------------------

def load_history(history_path):
    """Prend l'historique pour en faire un DataFrame (tableau de panda) avec colonnes :
    iteration | rmse | <org>__<param> ... | bio__<espèce> ..."""
    if not os.path.exists(history_path):
        raise FileNotFoundError(f"historical not found : {history_path}")

    with open(history_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    df = pd.DataFrame(data)
    print(f"Historical loaded : {len(df)} évaluations.")

    param_cols = [c for c in df.columns if '__' in c and not c.startswith('bio__')]
    bio_cols   = [c for c in df.columns if c.startswith('bio__')]
    print(f"  {len(param_cols)} paramètre(s) calibré(s), {len(bio_cols)} espèce(s) suivie(s).")

    return df, param_cols, bio_cols



#Courbe de "convergence" 
#(c'est plus est ce que le seuil est respecté mais quand j'ai codé je trouvais pas le bon mot)
#-----------------------------------------------------------------------------

def plot_convergence(df, output_path=None):
    #Trace l'évolution de la RMSE au fil des itérations.

    fig, ax = plt.subplots(figsize=(10, 4))
    _apply_style(fig, ax)

    rmse = df["rmse"].values
    iters = df["iteration"].values

    ax.plot(iters, rmse, color=STYLE["accent"], linewidth=1.2, alpha=0.8, label="RMSE")

    # Meilleur point
    best_idx = np.argmin(rmse)
    ax.scatter(iters[best_idx], rmse[best_idx],
               color=STYLE["good"], s=80, zorder=5,
               label=f"Meilleur : {rmse[best_idx]:.4f} (iter {iters[best_idx]})")

    # Ligne du minimum en pointillé
    ax.axhline(rmse[best_idx], color=STYLE["good"],
               linewidth=0.8, linestyle='--', alpha=0.5)

    ax.set_xlabel("Itération")
    ax.set_ylabel("RMSE normalisée")
    ax.set_title("Convergence de l'optimisation")
    legend = ax.legend(facecolor=STYLE["ax_bg"], edgecolor=STYLE["grid"],
                       labelcolor=STYLE["text"])

    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor=STYLE["fig_bg"])
        print(f"Convergence sauvegardée : {os.path.basename(output_path)}")
    else:
        plt.show()

    plt.close(fig)

#Graphe paramètre -> Biomasses(toutes espèces) (très gourmand)
#----------------------------------------------------------------------------------

def plot_param_vs_biomasses(df, param_col, bio_cols,
                             output_path=None, highlight_best=True):
    """Pour un paramètre donné (abscisse), trace la biomasse finale
    de chaque espèce (ordonnée) sur le même graphe
    Les points sont colorés par RMSE (du pire au meilleur)
    param_col  : colonne paramètre, ex 'Shrimps__KMort'
    bio_cols   : liste des colonnes biomasse"""
    n_species = len(bio_cols)
    if n_species == 0:
        print("Aucune espèce dans l'historique.")
        return

    # Couleur des points selon RMSE (plasma inversé -> jaune = meilleur)
    rmse_vals = df["rmse"].values
    norm      = Normalize(vmin=rmse_vals.min(), vmax=np.percentile(rmse_vals, 90))
    cmap      = plt.get_cmap(STYLE["cmap"])

    # Nom court pour que ça rentre pour le titre
    param_short = param_col.split('__')[-1]
    org_name    = param_col.split('__')[0]

    fig, axes = plt.subplots(
        nrows=max(1, (n_species + 2) // 3),
        ncols=min(3, n_species),
        figsize=(5 * min(3, n_species), 4 * max(1, (n_species + 2) // 3)),
        squeeze=False
    )
    axes_flat = axes.flatten()
    _apply_style(fig, axes_flat)

    x_vals = df[param_col].values

    for k, bio_col in enumerate(bio_cols):
        ax = axes_flat[k]
        y_vals = df[bio_col].values

        colors = cmap(norm(rmse_vals))
        sc = ax.scatter(x_vals, y_vals, c=colors, s=18, alpha=0.75, edgecolors='none')

        # Meilleur point
        if highlight_best:
            best_idx = np.argmin(rmse_vals)
            ax.scatter(x_vals[best_idx], y_vals[best_idx],
                       color=STYLE["good"], s=80, zorder=5,
                       edgecolors='white', linewidths=0.5)

        species_label = bio_col.replace("bio__", "")
        ax.set_xlabel(f"{param_short} [{org_name}]", fontsize=8)
        ax.set_ylabel("Biomasse finale", fontsize=8)
        ax.set_title(species_label, fontsize=9)

    # Masquer les axes vides
    for k in range(n_species, len(axes_flat)):
        axes_flat[k].set_visible(False)

    # Colorbar RMSE sur un axe dédié — évite le conflit avec tight_layout
    fig.subplots_adjust(top=0.90, hspace=0.50, wspace=0.38, right=0.88)
    cbar_ax = fig.add_axes([0.91, 0.15, 0.02, 0.65])  # [left, bottom, width, height]
    cbar_ax.set_facecolor(STYLE["ax_bg"])

    sm = ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, cax=cbar_ax)
    cbar.set_label("RMSE normalisée", color=STYLE["text"])
    cbar.ax.yaxis.set_tick_params(color=STYLE["text"])
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=STYLE["text"])
    cbar.outline.set_edgecolor(STYLE["grid"])

    fig.suptitle(
        f"Influence de {param_short} ({org_name}) sur les biomasses",
        color=STYLE["text"], fontsize=11
    )

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor=STYLE["fig_bg"])
        print(f"Graphe sauvegardé : {os.path.basename(output_path)}")
    else:
        plt.show()

    plt.close(fig)

#heatmap de correlation paramètres/biomasses (potentiellement peu utile)
#------------------------------------------------------------------------------

def plot_correlation_heatmap(df, param_cols, bio_cols, output_path=None):

    if not param_cols or not bio_cols:
        print("Pas assez de données pour la heatmap.")
        return

    # Matrice de corrélation
    corr_matrix = np.zeros((len(param_cols), len(bio_cols)))
    for i, pc in enumerate(param_cols):
        for j, bc in enumerate(bio_cols):
            x = df[pc].values
            y = df[bc].values
            mask = np.isfinite(x) & np.isfinite(y)
            if mask.sum() > 2:
                corr_matrix[i, j] = np.corrcoef(x[mask], y[mask])[0, 1]

    param_labels   = [c.replace('__', '\n') for c in param_cols]
    species_labels = [c.replace('bio__', '') for c in bio_cols]

    fig, ax = plt.subplots(figsize=(max(6, len(bio_cols) * 1.4),
                                    max(4, len(param_cols) * 0.9)))
    _apply_style(fig, ax)

    im = ax.imshow(corr_matrix, cmap='RdBu_r', vmin=-1, vmax=1, aspect='auto')

    # Annotations numériques
    for i in range(len(param_cols)):
        for j in range(len(bio_cols)):
            val = corr_matrix[i, j]
            color = 'white' if abs(val) > 0.5 else STYLE["text"]
            ax.text(j, i, f"{val:.2f}", ha='center', va='center',
                    fontsize=7, color=color)

    ax.set_xticks(range(len(bio_cols)))
    ax.set_xticklabels(species_labels, rotation=40, ha='right', fontsize=8)
    ax.set_yticks(range(len(param_cols)))
    ax.set_yticklabels(param_labels, fontsize=8)
    ax.set_title("Corrélation paramètres / biomasses finales",
                 color=STYLE["text"], fontsize=11)

    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Corrélation de Pearson", color=STYLE["text"])
    cbar.ax.yaxis.set_tick_params(color=STYLE["text"])
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=STYLE["text"])
    cbar.outline.set_edgecolor(STYLE["grid"])

    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor=STYLE["fig_bg"])
        print(f"Heatmap sauvegardée : {os.path.basename(output_path)}")
    else:
        plt.show()

    plt.close(fig)

# VI vs VF de la meilleure run
#-----------------------------------------------------------------------

def plot_best_run_comparison(df, initial_conditions, output_path=None):


    best_row = df.loc[df["rmse"].idxmin()]
    bio_cols = [c for c in df.columns if c.startswith('bio__')]

    species   = []
    vi_vals   = []
    vf_vals   = []
    converges = []

    for bc in bio_cols:
        sp_name = bc.replace('bio__', '')
        vf = best_row[bc]

        # Matching avec initial_conditions
        vi = None
        if sp_name in initial_conditions:
            vi = initial_conditions[sp_name]
        else:
            for key, val in initial_conditions.items():
                match = re.search(r'\[([^\]]+)\]', key)
                if match and match.group(1) == sp_name:
                    vi = val
                    break
                if len(sp_name) > 3 and (sp_name.lower() in key.lower()
                                          or key.lower() in sp_name.lower()):
                    vi = val
                    break

        if vi is None or vi <= 0:
            continue

        ecart_pct  = abs(vf - vi) / vi * 100
        species.append(sp_name)
        vi_vals.append(vi)
        vf_vals.append(vf)
        converges.append(ecart_pct < 20.0)

    if not species:
        print("No species to trace for the VI/V_final balance sheet")
        return

    n  = len(species)
    x  = np.arange(n)
    w  = 0.35

    fig, ax = plt.subplots(figsize=(max(8, n * 0.9), 5))
    _apply_style(fig, ax)

    bar_colors = [STYLE["good"] if c else STYLE["bad"] for c in converges]

    ax.bar(x - w/2, vi_vals, w, label="VI (cible)",
           color=STYLE["accent"], alpha=0.85, edgecolor='none')
    ax.bar(x + w/2, vf_vals, w, label="V_final (simulated)",
           color=bar_colors, alpha=0.85, edgecolor='none')

    ax.set_xticks(x)
    ax.set_xticklabels(species, rotation=35, ha='right', fontsize=8)
    ax.set_ylabel("Biomass")
    ax.set_title(f"Best run: VI vs V_final  (RMSE = {best_row['rmse']:.4f})")

    legend = ax.legend(facecolor=STYLE["ax_bg"], edgecolor=STYLE["grid"],
                       labelcolor=STYLE["text"])

    # Annotations écart %
    for i, (vi, vf, conv) in enumerate(zip(vi_vals, vf_vals, converges)):
        ep = abs(vf - vi) / vi * 100
        ax.text(x[i] + w/2, max(vi, vf) * 1.02,
                f"{ep:.0f}%", ha='center', fontsize=7,
                color=STYLE["good"] if conv else STYLE["bad"])

    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor=STYLE["fig_bg"])
        print(f"Visual balance sheet saved : {os.path.basename(output_path)}")
    else:
        plt.show()

    plt.close(fig)

#fonction principale qui appelle tout les grpahes
#-----------------------------------------------------------------------------

def generate_all_plots(history_path, initial_conditions, output_dir):
    os.makedirs(output_dir, exist_ok=True)

    df, param_cols, bio_cols = load_history(history_path)

    plot_convergence(
        df,
        output_path=os.path.join(output_dir, "01_convergence.png")
    )

    plot_correlation_heatmap(
        df, param_cols, bio_cols,
        output_path=os.path.join(output_dir, "02_heatmap_correlation.png")
    )

    plot_best_run_comparison(
        df, initial_conditions,
        output_path=os.path.join(output_dir, "03_bilan_best_run.png")
    )

    print(f"\nTous les graphes générés dans : {output_dir}")
