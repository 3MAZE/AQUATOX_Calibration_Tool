How to install AQUATOX_Calibration_Tool:
1. Download the files
2. Open a CMD
3. activate your python environnement in the CMD (for example: conda activate science if your using a anaconda environnement named science)
4. go to your files in the CMD using: cd C:\Users\Desktop\AQ_CA_Tool_zipped (for example)
5. once you have something like this : (science) C:\Users\Desktop\AQ_CA_Tool_zipped> in your CMD, add this command : pyinstaller --onefile --windowed --name "AQ_Cal_Tool" --add-data  "ui;ui" --icon=aquatox_CT_icon.ico main.py (also available in the main.py)


How to use AQUATOX_Calibration_Tool:
1. Open it
2. Put the path for AQUATOX, the file where your want to work, and your model (in .txt)
3. Run a basic simulation to see every gap between your initial and final values for every species 
4. Check/uncheck the parameters you want to calibrated, or to analyze the sensibility. Change the tolerance as you want.
5. Calibrate your parameters choosing the optimisation parameters you want (read the help points in the app)
6. Visualise the change by clicking on the blue link in the logs once the calibration is done.
7. Visualise, analyse and save your results with all the excels that will be created in your file.

What I personnaly do to calibrate models efficiently:
1. Run a sensitivity analysis on all parameters.
2. Select the parameters that are over any% you want (usually taking all the parameters that makes a 1% variation on at least 1 specie while variating by 10%. It's only 1/3 off the global parameters), there is a feature to do so in the app, click on the blue link in the log once the sensiblity analysis is done.
3. Run a calibration with those parameters selected. Use DE algorithm for quick wide search (with low numbers of parameters (put blocs of 10/15), and low number of iterations), or use CMA-ES for big blocs of parameters and fast results (300 iterations, blocs of 30 parameters)

/!\ This tool is a new born and hasn't be tested a lot, it's not magical, it won't fix everything, it only fix the details so it don't break the reality of your model for blind optimization. However, if the error isn't going down enough, check the parameters which have been changed by +-10%, it might give indication, on where you can reduce error, but be careful about changing the realism of the species.
This lead to a second /!\
/!\ by default tolerance is 10%, putting an already calibrated model in AQUATOX_CAll_Tool means that parameters that as been stopped by those 10% are no longer stopped and might go over.
For example:
On a model Shrimp Kmort=1. After a calibration Kmort=1.1 (+10%), if you calibrate again without closing AQUATOX_Cal_Tool no problem it can't go higher if you haven't decided so.
But if you close AQUATOX_Cal_Tool, open your model with Shrimp Kmort=1.1 and launch a calibration with Kmort, it can now go between 0.99 and 1.21. 

