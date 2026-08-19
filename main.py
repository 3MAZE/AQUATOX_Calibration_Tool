# -*- coding: utf-8 -*-

"""
Created on Thu May 7 10:11:24 2026

@author: eloua
"""
#Compiler: pyinstaller --onefile --windowed --name "AQ_Cal_Tool" --add-data  "ui;ui" --icon=aquatox_CT_icon.ico main.py

import sys
import os
import webview
from api import AquatoxAPI
#Ce main a été fait par CLAUDE AI
# Chemin de base (fonctionne aussi depuis le .exe PyInstaller) 
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UI_PATH = os.path.join(BASE_DIR, "ui", "index.html")

if __name__ == "__main__":
    api    = AquatoxAPI()
    window = webview.create_window(
        title     = "AQUATOX Calibration Tool",
        url       = UI_PATH,
        js_api    = api,
        width     = 1300,
        height    = 860,
        min_size  = (1000, 700),
        resizable = True,
    )
    # Injecter la référence window dans l'API pour les callbacks JS
    api._window = window

    webview.start(debug=False)
