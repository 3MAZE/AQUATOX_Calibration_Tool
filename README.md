How to install AQUATOX_Calibration_Tool:
1. Download the files
2. Open a CMD
3. activate your python environnement in the CMD (for example: conda activate science if your using a anaconda environnement named science)
4. go to your files in the CMD using: cd C:\Users\Desktop\AQ_CA_Tool_zipped (for example)
5. once you have something like this : (science) C:\Users\Desktop\AQ_CA_Tool_zipped> in your CMD, add this command : pyinstaller --onefile --windowed --name "AQ_Cal_Tool" --add-data  "ui;ui" --icon=aquatox_CT_icon.ico main.py
6. (also available in the main.py)
