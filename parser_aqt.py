# -*- coding: utf-8 -*-
"""
Created on Thu May 7 10:37:51 2026

@author: eloua
"""

import re
import os
import subprocess
import pandas as pd
import threading
import time
import uuid

# Constantes de flag Windows
CREATE_NO_WINDOW  = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010

# PARAMETRES ANIMAUX  (clé technique -> nom lisible pour l'Excel)
# Paramètres numériques calibrables pour tous les animaux
# Ajouter d'autres paramètres si besoin
ANIMAL_PARAMS = {
    "InitialCond"       : "Initial Biomass",
    "CMax"              : "Maximum Consumption",
    "EndogResp"         : "Endogenous Respiration",
    "KMort"             : "Mortality Coefficient",
    "KResp"             : "Specific Dynamic Action",
    "FHalfSat"          : "Half Saturation Feeding",
    "Bmin"              : "Min Prey for Feeding",
    "Q10"               : "Temp Response Slope",
    "TOpt"              : "Optimum Temperature",
    "TMax"              : "Maximum Temperature",
    "TRef"              : "Min Adaptation Temp",
    "KExcr"             : "Excretion:Respiration", 
    "N2OrgInit"         : "N to Organics",
    "P2OrgInit"         : "P to Organics",
    "Wet2Dry"           : "Wet to Dry",
    "PctGamete"         : "Gamete : Biomass",
    "GMort"             : "Gamete Mortality",
    "MeanWeight"        : "Mean Wet Weight",
    "PctEmbedThreshold" : "Percent Embeddedness Threshold",
    "KCap"              : "Carrying Capacity",
    "AveDrift"          : "Average Drift",
    "Trigger"           : "Trigger: Deposition Rate",
    "VelMax"            : "VelMax", 
    "LifeSpan"          : "Mean lifespan", 
    "FishFracLipid"     : "Fraction that is lipid", 
    "O2_LethalConc"     : "Low O2: Lethal Conc", 
    "O2_LethalPct"      : "Low O2: Pct. Killed", 
    "O2_EC50growth"     : "Low O2: EC50 Growth", 
    "O2_EC50repro"      : "Low O2: EC50 Reproduction", 
    "Ammonia_LC50"      : "Ammonia Toxicity: LC50", 
    "RA"                : "RA",
    "RB"                : "RB",
    "RQ"                : "RQ",
    "RTL"               : "RTL",
    "ACT"               : "ACT",
    "RTO"               : "RTO",
    "RK1"               : "RK1",
    "BACT"              : "BACT",
    "RTM"               : "RTM",
    "RK4"               : "RK4",
    "ACT"               : "ACT",
    "SlopeSSFeed"       : "Slope for Sed. Response", 
	"InterceptSSFeed"   : "Intercept for Sed. Resp.", 
}

# PARAMETRES PLANTES  (type de plante -> {clé technique -> nom lisible})
# Chaque type n'expose que les paramètres qu'il a
# Types disponible actuellement: "Phytoplankton", "Periphyton", "Macrophyte"
PLANT_PARAMS = {
    "Phytoplankton": {
        "InitialCond"      : "Initial Biomass",
        "KPO4"             : "P Half-saturation", 
        "KN"               : "N Half-saturation",
        "Q10"              : "Temp Response Slope",
        "TOpt"             : "Optimum Temperature",
        "TMax"             : "Maximum Temperature",
        "TRef"             : "Min Adaptation Temp", 
        "KCarbon"          : "Inorg C Half-saturation", 
        "PMax"             : "Max. Photosynthesis Rate", 
        "Resp20"           : "Resp Rate at 20 deg. C", 
        "EMort"            : "Exponential Mort Coeff", 
        "P2Org"            : "P to Photosynthate", 
        "N2Org"            : "N to Photosynthate", 
        "ECoeffPhyto"      : "Light Extinction", 
        "Wet2Dry"          : "Wet to Dry", 
        "PlantFracLipid"   : "Fraction that is lipid", 
        "NHalfSatInternal" : "N Half-saturation Internal", 
        "PHalfSatInternal" : "P Half-saturation Internal", 
        "MaxNUptake"       : "N Max Uptake Rate",
        "MaxPUptake"       : "P Max Uptake Rate", 
        "Min_N_Ratio"      : "Min N Ratio", 
        "Min_P_Ratio"      : "Min P Ratio",
        "MaxLightSat"      : "Max. Saturating Light", 
        "MinLightSat"      : "Min. Saturating Light", 
        "Plant_to_Chla"    : "Phytoplankton: C:Chlorophyll a", 
        "KSed"             : "Phytoplankton: Sedimentation Rate (KSed)", 
        "KSedTemp"         : "Phytoplankton: Temperature of Obs. KSed", 
        "KSedSalinity"     : "Phytoplankton: Salinity of Obs. KSed", 
        "ESed"             : "Phytoplankton: Exp. Sedimentation Coeff", 
    },
    "Periphyton": {
        "InitialCond"      : "Initial Biomass",
        "KPO4"             : "P Half-saturation", 
        "KN"               : "N Half-saturation", 
        "Q10"              : "Temp Response Slope",
        "TOpt"             : "Optimum Temperature",
        "TMax"             : "Maximum Temperature",
        "TRef"             : "Min Adaptation Temp",
        "KCarbon"          : "Inorg C Half-saturation", 
        "PMax"             : "Max. Photosynthesis Rate", 
        "Resp20"           : "Resp Rate at 20 deg. C", 
        "EMort"            : "Exponential Mort Coeff", 
        "P2Org"            : "P to Photosynthate", 
        "N2Org"            : "N to Photosynthate", 
        "ECoeffPhyto"      : "Light Extinction", 
        "Wet2Dry"          : "Wet to Dry", 
        "PlantFracLipid"   : "Fraction that is lipid", 
        "NHalfSatInternal" : "N Half-saturation Internal", 
        "PHalfSatInternal" : "P Half-saturation Internal", 
        "MaxNUptake"       : "N Max Uptake Rate",
        "MaxPUptake"       : "P Max Uptake Rate", 
        "Min_N_Ratio"      : "Min N Ratio", 
        "Min_P_Ratio"      : "Min P Ratio",
        "MaxLightSat"      : "Max. Saturating Light", 
        "MinLightSat"      : "Min. Saturating Light",
        "Red_Still_Water"  : "Periphyton: Reduction in Still Water", 
        "FCrit"            : "Periphyton: Critical Force (FCrit)",
    },
    "Macrophyte": {
        "InitialCond"      : "Initial Biomass",
        "KPO4"             : "P Half-saturation", 
        "KN"               : "N Half-saturation", 
        "Q10"              : "Temp Response Slope",
        "TOpt"             : "Optimum Temperature",
        "TMax"             : "Maximum Temperature",
        "TRef"             : "Min Adaptation Temp",
        "KCarbon"          : "Inorg C Half-saturation", 
        "PMax"             : "Max. Photosynthesis Rate", 
        "Resp20"           : "Resp Rate at 20 deg. C", 
        "EMort"            : "Exponential Mort Coeff", 
        "P2Org"            : "P to Photosynthate", 
        "N2Org"            : "N to Photosynthate", 
        "ECoeffPhyto"      : "Light Extinction", 
        "Wet2Dry"          : "Wet to Dry", 
        "PlantFracLipid"   : "Fraction that is lipid", 
        "NHalfSatInternal" : "N Half-saturation Internal", 
        "PHalfSatInternal" : "P Half-saturation Internal", 
        "MaxNUptake"       : "N Max Uptake Rate",
        "MaxPUptake"       : "P Max Uptake Rate", 
        "Min_N_Ratio"      : "Min N Ratio", 
        "Min_P_Ratio"      : "Min P Ratio",
        "MaxLightSat"      : "Max. Saturating Light", 
        "MinLightSat"      : "Min. Saturating Light",
        "Carry_Capac"      : "Macrophytes: Carrying Capacity", 
        "Macro_VelMax"     : "Macrophytes: VelMax",
    },
    # Type defaut si le type n'est pas reconnu
    "__default__": {
        "InitialCond"      : "Initial Biomass",
        "KPO4"             : "P Half-saturation", 
        "KN"               : "N Half-saturation", 
        "Q10"              : "Temp Response Slope",
        "TOpt"             : "Optimum Temperature",
        "TMax"             : "Maximum Temperature",
        "TRef"             : "Min Adaptation Temp",
        "KCarbon"          : "Inorg C Half-saturation", 
        "PMax"             : "Max. Photosynthesis Rate", 
        "Resp20"           : "Resp Rate at 20 deg. C", 
        "EMort"            : "Exponential Mort Coeff", 
        "P2Org"            : "P to Photosynthate", 
        "N2Org"            : "N to Photosynthate", 
        "ECoeffPhyto"      : "Light Extinction", 
        "Wet2Dry"          : "Wet to Dry", 
        "PlantFracLipid"   : "Fraction that is lipid", 
        "NHalfSatInternal" : "N Half-saturation Internal", 
        "PHalfSatInternal" : "P Half-saturation Internal", 
        "MaxNUptake"       : "N Max Uptake Rate",
        "MaxPUptake"       : "P Max Uptake Rate", 
        "Min_N_Ratio"      : "Min N Ratio", 
        "Min_P_Ratio"      : "Min P Ratio",
        "MaxLightSat"      : "Max. Saturating Light", 
        "MinLightSat"      : "Min. Saturating Light", 
    },
}

# TOLERANCES PAR DEFAUT (+-%) commun animaux et plantes
# A potentiellement affiner avec la littérature
DEFAULT_TOLERANCE = {
    "InitialCond"       : 0.0,    #Pas touche à la VI (pas de sens) elle est la à titre indicatif
    "CMax"              : 0.1,
    "EndogResp"         : 0.1,
    "KMort"             : 0.1,
    "KResp"             : 0.1,
    "FHalfSat"          : 0.1,
    "Bmin"              : 0.1,
    "Q10"               : 0.1,
    "TOpt"              : 0.1,
    "TMax"              : 0.1,
    "TRef"              : 0.1,
    "KExcr"             : 0.1, 
    "N2OrgInit"         : 0.1,
    "P2OrgInit"         : 0.1,
    "Wet2Dry"           : 0.1,
    "PctGamete"         : 0.1,
    "GMort"             : 0.1,
    "MeanWeight"        : 0.1,
    "PctEmbedThreshold" : 0.1,
    "KCap"              : 0.1,
    "AveDrift"          : 0.1,
    "Trigger"           : 0.1,
    "VelMax"            : 0.1, 
    "LifeSpan"          : 0.1, 
    "FishFracLipid"     : 0.1, 
    "O2_LethalConc"     : 0.1,
    "O2_LethalPct"      : 0.1,
    "O2_EC50growth"     : 0.1,
    "O2_EC50repro"      : 0.1,
    "Ammonia_LC50"      : 0.1,
    "RA"                : 0.1,
    "RB"                : 0.1,
    "RQ"                : 0.1,
    "RTL"               : 0.1,
    "ACT"               : 0.1,
    "RTO"               : 0.1,
    "RK1"               : 0.1,
    "BACT"              : 0.1,
    "RTM"               : 0.1,
    "RK4"               : 0.1,
    "SlopeSSFeed"       : 0.1,
    "InterceptSSFeed"   : 0.1,
    "KPO4"              : 0.1, 
    "KN"                : 0.1,
    "KCarbon"           : 0.1,
    "PMax"              : 0.1,
    "Resp20"            : 0.1,
    "EMort"             : 0.1,
    "P2Org"             : 0.1,
    "N2Org"             : 0.1,
    "ECoeffPhyto"       : 0.1,
    "PlantFracLipid"    : 0.1,
    "NHalfSatInternal"  : 0.1,
    "PHalfSatInternal"  : 0.1,
    "MaxNUptake"        : 0.1,
    "MaxPUptake"        : 0.1,
    "Min_N_Ratio"       : 0.1,
    "Min_P_Ratio"       : 0.1,
    "MaxLightSat"       : 0.1,
    "MinLightSat"       : 0.1,
    "Carry_Capac"       : 0.1,
    "Macro_VelMax"      : 0.1,
    "Red_Still_Water"   : 0.1,
    "FCrit"             : 0.1,
    "KSed"              : 0.1,
    "KSedTemp"          : 0.1,
    "KSedSalinity"      : 0.1,
    "ESed"              : 0.1,
}

# paramètres "groupés"  commun animaux + plantes
# Si le booléen vaut FALSE dans le .txt, les paramètres de la liste sont exclus
# de l'Excel pour cette espèce
# Clé   = nom exact du champ booléen dans le .txt AQUATOX
# Valeur = liste des clés numériques dépendantes à silencer
BOOL_DEPENDENCIES = {
    # Animaux
    "SuspSedFeeding"  : ["SlopeSSFeed", "InterceptSSFeed"],
    "SenstoPctEmbed"  : ["PctEmbedThreshold"],
    "UseAllom_C"      : ["CA", "CB"],
    "UseAllom_R"      : ["RA", "RB"],
    "UseSet1"         : ["RQ", "RTO", "RTM", "RTL", "RK1", "RK4", "ACT", "BACT"],
    # Plantes 
    "UseAdaptiveLight": ["MaxLightSat", "MinLightSat"],
}

# paramètres où valeur=0 signifie "désactivé" commun animaux + plantes
# Exclus de l'Excel si leur valeur est exactement 0.0
ZERO_MEANS_DISABLED = {
    # Animaux
    "Burrow_Index",
    "AveDrift",
    "Placeholder",
    "SlopeSSFeed",
    "InterceptSSFeed",
    "PrefRiffle",
    "PrefPool",
    # Plantes
    "CarryCapac",
    "Red_Still_Water",
    "Macro_Drift",
    "Macro_VelMax",
    "FCrit",
    "KSedTemp",
    "KSedSalinity",
    "Min_P_Ratio",
}


# Parser: adapter la donnée d'un format à un autre
#-----------------------------------------------------------------------------------------
class AquatoxHybridParser:
    """
    Lit le fichier .txt AQUATOX et délimite les blocs par organisme.
    Extrait les paramètres cibles (animaux ou plantes selon le type détecté)
    en appliquant les filtres booléens et les règles valeur=0.
    """

    def __init__(self, file_path):
        self.file_path      = file_path
        self.all_lines      = []
        # Dict : nom_organisme -> {"start": int, "end": int, "params": dict}
        self.organism_zones = {}

    # -------------------------------------------------------------------------
    def scan_file(self):
        print(f"Lecture de {os.path.basename(self.file_path)}...")    
        with open(self.file_path, 'r', encoding='latin-1') as f:
            self.all_lines = f.readlines()
    
        EXCLUDED_NAMES = ["pH", "Temperature", "Salinity", "Light",
                          "Wind Loading", "Water Volume"]
    
        pname_positions = []
        for i, line in enumerate(self.all_lines):
            if '"PName^":' in line:
                match = re.search(r'"PName\^":\s*"([^"]+)"', line)
                if match:
                    name = match.group(1)
                    if name not in EXCLUDED_NAMES:
                        pname_positions.append((i, name))
    
        n = len(self.all_lines)
        cursor = 0
        for idx, (line_num, org_name) in enumerate(pname_positions):
            start = cursor
            next_pname_line = (pname_positions[idx + 1][0] if idx + 1 < len(pname_positions) else n)
            same_species_line = None
            for j in range(line_num, next_pname_line):
                if '"PSameSpecies^"' in self.all_lines[j]:
                    same_species_line = j
                    break
    
            if same_species_line is not None:
                end = same_species_line
            else:
                """ Pas de Record derrière (chimique, détritus, Undisplayed) :
                 on trouve où cette entité se fini vraiment,
                sans empiéter sur les lignes du PName^ suivant"""
                end = self._find_block_end(line_num)
    
            self.organism_zones[org_name] = {
                "start"  : start,
                "end"    : end,
                "params" : {}
            }
            cursor = end + 1
        for org_name, zone in self.organism_zones.items():
            zone["params"] = self._extract_params(zone["start"], zone["end"])
    
        print(f"{len(self.organism_zones)} organismes détectés.")
        return list(self.organism_zones.keys())
    # -------------------------------------------------------------------------
    def _find_block_end(self, start_line):
        """ cherche la fin de son bloc en comptant
        les accolades. le fallback c'est la fin du fichier"""
        depth = 0
        for i in range(start_line, len(self.all_lines)):
            depth += self.all_lines[i].count('{')
            depth -= self.all_lines[i].count('}')
            if depth < 0:
                return i
        return len(self.all_lines) - 1

    # -------------------------------------------------------------------------
    def _extract_params(self, start, end):
        """
        Extrait les paramètres cibles dans un bloc de lignes.
        Étape 1 — Détecte si le bloc est un animal ou une plante,
                  et si plante : quel type (PlantType).
        Étape 2 — Lit les paramètres numériques du dict correspondant
                  (ANIMAL_PARAMS ou PLANT_PARAMS[type]).
        Étape 3 — Lit les booléens de BOOL_DEPENDENCIES.
        Retourne un dict :
            clé_technique      -> {"valeur": float, "source": str, "ligne": int}
            "__type"           -> "animal" | "plant"
            "__plant_type"     -> str  (seulement pour les plantes)
            "__bool_<BoolKey>" -> True | False """

        block_lines = self.all_lines[start:end + 1]
        params      = {}

        # Étape 1 
        # ------------------------------------------------------------------
        is_plant   = False
        plant_type = None
        for line in block_lines:
            if '"AnimalRecord"' in line or '"AnimalName"' in line:
                is_plant = False
                break
            if '"PlantRecord"' in line or '"PlantName"' in line:
                is_plant = True
                break

        if is_plant:
            params["__type"] = "plant"
            for line in block_lines:
                if '"PlantType":' in line:
                    m = re.search(r'"PlantType":\s*"([^"]+)"', line)
                    if m:
                        plant_type = m.group(1).strip()
                    break
            params["__plant_type"] = plant_type if plant_type else "unknown"
        else:
            params["__type"] = "animal"

        # Étape 2 
        # ------------------------------------------------------------------
        if is_plant:
            # Cherche le type exact, sinon  le fallback 
            param_dict = PLANT_PARAMS.get(plant_type, PLANT_PARAMS["__default__"])
        else:
            param_dict = ANIMAL_PARAMS

        # Extraction des valeurs numériques
        for tech_key in param_dict:
            for j, line in enumerate(block_lines):
                if f'"{tech_key}":' in line:
                    val_match = re.search(rf'"{tech_key}":\s*(-?[\d\.E+\-]+)', line, re.IGNORECASE)                
                    if val_match:
                        source = ""
                        if j + 1 < len(block_lines):
                            src_match = re.search(rf'"X{tech_key}":\s*"([^"]*)"', block_lines[j + 1])
                            if src_match:
                                source = src_match.group(1).strip()

                        params[tech_key] = {
                            "valeur" : float(val_match.group(1)),
                            "source" : source,
                            "ligne"  : start + j   # ligne absolue dans all_lines
                        }
                        break

        # ------------------------------------------------------------------
        # Étape 3 : lecture des booléens conditionnels
        # ------------------------------------------------------------------
        for bool_key in BOOL_DEPENDENCIES:
            for line in block_lines:
                if f'"{bool_key}":' in line:
                    upper = line.upper()
                    if "FALSE" in upper:
                        params[f"__bool_{bool_key}"] = False
                    elif "TRUE" in upper:
                        params[f"__bool_{bool_key}"] = True
                    break

        return params

    # -------------------------------------------------------------------------
    def get_initial_conditions(self):
        """
        Retourne un dict : nom_organisme -> valeur InitialCond.
        Utilisé comme valeur cible pour la calibration.
        """
        ic = {}
        for org, zone in self.organism_zones.items():
            if "InitialCond" in zone["params"]:
                ic[org] = zone["params"]["InitialCond"]["valeur"]
        return ic

# Generation de l'excel
#---------------------------------------------------------------------------------------------
def generate_excel_config(parser, output_path):
    """
    Génère le fichier Excel listant tous les paramètres calibrables.
    Exclut automatiquement :
      - les clés internes (__type, __plant_type, __bool_*)
      - les paramètres désactivés par un booléen parent à FALSE
      - les paramètres dans ZERO_MEANS_DISABLED dont la valeur est 0.0
    """
    rows = []

    for org_name, zone in parser.organism_zones.items():

        # Choix du dict lisible selon animal/plante
        org_type   = zone["params"].get("__type", "animal")
        plant_type = zone["params"].get("__plant_type", None)

        if org_type == "plant":
            param_dict = PLANT_PARAMS.get(plant_type, PLANT_PARAMS["__default__"])
        else:
            param_dict = ANIMAL_PARAMS

        # Paramètres désactivés par un booléen parent FALSE
        disabled = set()
        for bool_key, deps in BOOL_DEPENDENCIES.items():
            flag = zone["params"].get(f"__bool_{bool_key}", True)
            if flag is False:
                disabled.update(deps)

        # Itération sur les paramètres de l'espèce
        for tech_key, info in zone["params"].items():

            # Ignorer toutes les clés internes
            if tech_key.startswith("__"):
                continue

            # Ignorer si désactivé par un booléen parent
            if tech_key in disabled:
                continue

            # Ignorer si valeur=0 et paramètre dans ZERO_MEANS_DISABLED
            if tech_key in ZERO_MEANS_DISABLED and info["valeur"] == 0.0:
                continue

            # Nom lisible : on cherche d'abord dans le dict du type, puis fallback
            human_name  = param_dict.get(tech_key, tech_key)
            tol_default = DEFAULT_TOLERANCE.get(tech_key, 0.20)
            rows.append({
                "Groupe"              : org_name,
                "Type"                : org_type.capitalize(),
                "Paramètre"           : human_name,
                "Clé technique"       : tech_key,
                "Valeur actuelle"     : info["valeur"],
                "Source littéraire"   : info["source"],
                "A CALIBRER (OUI/NON)": "NON" if tech_key == "InitialCond" else "NON",
                "Tolérance (%)"       : int(tol_default * 100),
            })
    df = pd.DataFrame(rows)

    # Mise en forme Excel
    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Calibration")
        ws = writer.sheets["Calibration"]
        col_widths = [30, 10, 28, 18, 18, 50, 22, 15]
        for col_idx, width in enumerate(col_widths, start=1):
            ws.column_dimensions[
                ws.cell(row=1, column=col_idx).column_letter
            ].width = width

    print(f"Excel généré : {output_path}")

# maj du fichier.txt grâce à l'excel
# -------------------------------------------------------------------------------------------
def update_txt_from_excel(parser, excel_path, output_txt_path):
    """Relit l'Excel rempli par l'utilisateur et met à jour les valeurs
    dans une copie du fichier .txt original.
    Ne touche qu'aux lignes concernées — tout le reste est conservé à l'identique"""    
    df = pd.read_excel(excel_path)
    # On ne garde que les lignes marquées OUI
    df_calib = df[df["A CALIBRER (OUI/NON)"].str.upper() == "OUI"].copy()
    if df_calib.empty:
        print("Aucun paramètre marqué OUI dans l'Excel. Rien à modifier.")
        return False

    new_lines = list(parser.all_lines)
    changes   = 0
    for _, row in df_calib.iterrows():
        org      = row["Groupe"]
        tech_key = row["Clé technique"]
        new_val  = float(str(row["Valeur actuelle"]).replace(',', '.'))
        if org not in parser.organism_zones:
            print(f"  ATTENTION : organisme '{org}' non trouvé dans le fichier.")
            continue
        param_info = parser.organism_zones[org]["params"].get(tech_key)
        if param_info is None:
            print(f"  ATTENTION : paramètre '{tech_key}' non trouvé pour '{org}'.")
            continue

        line_idx = param_info["ligne"]
        old_line = new_lines[line_idx]
        if f'"{tech_key}":' not in old_line:
            print(f"  ATTENTION : ligne {line_idx} inattendue pour '{tech_key}' / '{org}'.")
            continue

        indent    = old_line[: len(old_line) - len(old_line.lstrip())]
        has_comma = old_line.rstrip().endswith(',')
        suffix    = "," if has_comma else ""
        new_lines[line_idx] = f'{indent}"{tech_key}":  {new_val:.6E}{suffix}\n'
        changes += 1
        
    with open(output_txt_path, 'w', encoding='latin-1') as f:
        f.writelines(new_lines)

    print(f"{changes} modification(s) effectuée(s) -> {os.path.basename(output_txt_path)}")
    return True

# Runner aquatox
#--------------------------------------------------------------------------------------------
class AquatoxRunner:
    """Lance AQUATOX en mode ligne de commande.
    Attend la fin de la simulation avant de rendre la main"""
    def __init__(self, exe_path):
        self.exe_path = os.path.abspath(exe_path)
        self.exe_dir  = os.path.dirname(self.exe_path)
        if not os.path.exists(self.exe_path):
            raise FileNotFoundError(f"AQUATOX introuvable : {self.exe_path}")

    def run_simulation_visible(self, input_txt, output_csv, timeout=600):
        input_abs  = os.path.abspath(input_txt)
        output_abs = os.path.abspath(output_csv)
        exe_name   = os.path.basename(self.exe_path)   
        # Nom unique par appel — évite les collisions entre workers parallèles
        run_id   = uuid.uuid4().hex[:8]
        bat_path = os.path.join(self.exe_dir, f"_run_aquatox_temp_{run_id}.bat")  
        with open(bat_path, "w", encoding="ascii") as f:
            f.write('@echo off\n')
            f.write(f'cd /d "{self.exe_dir}"\n')
            f.write(f'"{exe_name}" ECEXP "{input_abs}" "{output_abs}"\n')
    
        command = f'start "AQUATOX Run" /wait /min cmd /c "{bat_path}"'
        try:
            subprocess.run(command, shell=True, cwd=self.exe_dir, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"Timeout ({timeout}s) dépassé.")
            return False
        except Exception as e:
            print(f"Erreur ouverture terminal : {e}")
            return False
        finally:
            if os.path.exists(bat_path):
                os.remove(bat_path)
    
        if os.path.exists(output_abs) and os.path.getsize(output_abs) > 0:
            print("Simulation terminée (CSV généré).")
            return True
        else:
            print("AQUATOX n'a pas généré le CSV attendu.")
            return False

    def run_simulation(self, input_txt, output_csv, timeout=600):
        input_abs  = os.path.abspath(input_txt)
        output_abs = os.path.abspath(output_csv)
        cmd        = [self.exe_path, "ECEXP", input_abs, output_abs]

        # Mode qui marche quand on passe par python (quand ya une console)
        # kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        # import sys, threading
        # stop_killer = threading.Event()
        # is_frozen   = getattr(sys, 'frozen', False)
        # if is_frozen and os.name == "nt":
        #     t = threading.Thread(target=self._popup_killer_frozen, args=(stop_killer,), daemon=True)
        #     t.start()
        # try:
        #     result = subprocess.run(
        #         cmd,
        #         cwd=self.exe_dir,
        #         capture_output=True,
        #         text=True,
        #         timeout=timeout,
        #         stdin=subprocess.DEVNULL,
        #         **kwargs
        #     )
        #     stdout = result.stdout + result.stderr
        #     if "Run Completed Successfully" in stdout:
        #         print("Simulation terminée avec succès.")
        #         return True
        #     elif os.path.exists(output_abs) and os.path.getsize(output_abs) > 0:
        #         print("Simulation terminée (CSV généré).")
        #         return True
        #     else:
        #         print("AQUATOX n'a pas confirmé le succès.")
        #         print(f"  Code retour : {result.returncode}")
        #         print(f"  Sortie : {stdout[:300]}")
        #         return False
        # except subprocess.TimeoutExpired:
        #     print(f"Timeout ({timeout}s) dépassé.")
        #     return False
        # except Exception as e:
        #     print(f"Erreur : {e}")
        #     return False
        # finally:
        #     stop_killer.set()
        return self.run_simulation_visible(input_abs, output_abs, timeout)
