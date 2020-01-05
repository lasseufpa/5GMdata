'''
This code gets the p2m files generated by InSite via simulation.py and writes in a database called episode.db.
One does not need to specify the number of scenes per episode because this information is obtained from
the JSON file and confirmed (redundancy) with the file 'sumoOutputInfoFileName.txt' at "run_dir".
'''
import os
import json
import numpy as np
import csv
from sys import argv

#AK-TODO we should provide support to specifying another name, instead of episode.db.

#now we don't need to recall config.py. We can simply specify folders below
#import config as c

from rwisimulation.tfrecord import UnexpectedCarsWithAntennaChangeError, SceneNotInEpisodeSequenceError, \
    EpisodeNotStartingFromZeroError
from rwimodeling import objects
from rwiparsing import P2mPaths
from rwiparsing import P2mCir

if len(argv) != 2:
    print('You need to specify the folder that has the output files written by the simulator!')
    print('Usage: python', argv[0], 'input_folder')
    exit(-1)

from rwisimulation.datamodel import save5gmdata as fgdb

def base_run_dir_fn(i): #the folders will be run00001, run00002, etc.
    """returns the `run_dir` for run `i`"""
    return "run{:05d}".format(i)

last_simulation_info = None
simulation_info = None
session = fgdb.Session()

# Object which will be modified in the RWI project
#base_insite_project_path = 'D:/insitedata/insite_new_simuls/'
#Folder to store each InSite project and its results (will create subfolders for each "run", run0000, run0001, etc.)
results_dir = argv[1] #'D:/owncloud-lasse/5GM_DATA/flat_simulation/results_new_lidar/'

#if use 3d detaileds models instead of boxes models
use_template = False

#The infos below typically does not change
if use_template:
    dst_object_file_nameBaseName = "random-line_.object"
else:
    dst_object_file_nameBaseName = "random-line.object"
#Ray-tracing output folder (where InSite will store the results (Study Area name)).
#They will be later copied to the corresponding output folder specified by results_dir
project_output_dirBaseName = 'study'
# Name (basename) of the paths file generated in the simulation
paths_file_name = 'model.paths.t001_01.r002.p2m'
#Output files, which are written by the Python scripts
# Name (basename) of the JSON output simulation info file
simulation_info_file_name = 'wri-simulation.info'

sc_i = 0
ep_i = -1 #it's summed to 1 and we need to start by 0
episode = None
#n_run = 100000
run_i = 0
while True:
#for run_i in range(100): # use the number of examples in config.py
    run_dir = os.path.join(results_dir, base_run_dir_fn(run_i))
    object_file_name = os.path.join(run_dir, dst_object_file_nameBaseName)
    #rays information but phase
    abs_paths_file_name = os.path.join(run_dir, project_output_dirBaseName, paths_file_name)
    if os.path.exists(abs_paths_file_name) == False:
        print('\nWarning: could not find file ', abs_paths_file_name, ' Stopping...')
        break
    #now we get the phase info from CIR file
    abs_cir_file_name = abs_paths_file_name.replace("paths","cir") #name for the impulse response (cir) file
    if os.path.exists(abs_cir_file_name) == False:
        print('ERROR: could not find file ', abs_cir_file_name)
        print('Did you ask InSite to generate the impulse response (cir) file?')
        exit(-1)

    abs_simulation_info_file_name = os.path.join(run_dir, simulation_info_file_name)
    with open(abs_simulation_info_file_name) as infile:
        simulation_info = json.load(infile)

    # start of episode
    if simulation_info['scene_i'] == 0:
        ep_i += 1
        this_scene_i = 0 #reset counter
        if episode is not None:
            session.add(episode)
            session.commit()

        #read SUMO information for this scene from text CSV file
        sumoOutputInfoFileName = os.path.join(run_dir,'sumoOutputInfoFileName.txt')
        with open(sumoOutputInfoFileName, 'r') as f:
            sumoReader = csv.reader(f) #AK-TODO ended up not using the CSV because the string is protected by " " I guess
            for row in sumoReader:
                headerItems = row[0].split(',')
                TsString = headerItems[-1]
                try:
                    Ts=TsString.split('=')[1]
                    timeString = headerItems[-2]
                    time=timeString.split('=')[1]
                except IndexError: #old format
                    Ts=0.005 #initialize values
                    time=-1
                break #process only first 2 rows / line AK-TODO should eliminate the loop
            for row in sumoReader:
                #secondRow = row[1].split(',')
                thisEpisodeNumber = int(row[0])
                if thisEpisodeNumber != ep_i:
                    print('ERROR: thisEpisodeNumber != ep_i. They are:', thisEpisodeNumber, 'and', ep_i,
                          'file: ', sumoOutputInfoFileName, 'read:', row)
                    exit(1)
                break #process only first 2 rows / line AK-TODO should eliminate the loop
        episode = fgdb.Episode(
            insite_pah=run_dir,
            sumo_path=sumoOutputInfoFileName,
            simulation_time_begin=time, #in milliseconds
            sampling_time=Ts, #in seconds
        )

    if episode is None:
        raise EpisodeNotStartingFromZeroError("From file {}".format(object_file_name))

    if simulation_info['scene_i'] != episode.number_of_scenes:
        raise SceneNotInEpisodeSequenceError('Expecting {} found {}'.format(
            len(episode.number_of_scenes),
            simulation_info['scene_i'],
        ))

    with open(object_file_name) as infile:
        obj_file = objects.ObjectFile.from_file(infile)
    print(abs_paths_file_name) #AK TODO take out this comment and use logging
    paths = P2mPaths(abs_paths_file_name)
    cir = P2mCir(abs_cir_file_name)

    scene = fgdb.Scene()
    # TODO read from InSite
    scene.study_area = ((0, 0, 0), (0, 0, 0))

    rec_i = 0
    for structure_group in obj_file:
        for structure in structure_group:
            for sub_structure in structure:
                object = fgdb.InsiteObject(name=structure.name)
                object.vertice_array = sub_structure.as_vertice_array()
                dimension_max = np.max(object.vertice_array, 0)
                dimension_min = np.min(object.vertice_array, 0)
                object.dimension = dimension_max - dimension_min
                object.position = dimension_max - (object.dimension / 2)

                if structure.name in simulation_info['cars_with_antenna']:
                    receiver = fgdb.InsiteReceiver()
                    if paths.get_total_received_power(rec_i+1) is not None:
                        receiver.total_received_power = paths.get_total_received_power(rec_i+1)
                        receiver.mean_time_of_arrival=paths.get_mean_time_of_arrival(rec_i+1)
                        receiver.position = object.position

                        phases = cir.get_phase_ndarray(rec_i+1) #get phases for all rays in degrees
                        rayIndex = 0
                        for departure, arrival, path_gain, arrival_time, interactions_list in zip(
                                paths.get_departure_angle_ndarray(rec_i+1),
                                paths.get_arrival_angle_ndarray(rec_i+1),
                                paths.get_p_gain_ndarray(rec_i+1),
                                paths.get_arrival_time_ndarray(rec_i+1),
                                paths.get_interactions_list(rec_i+1)):
                            ray = fgdb.Ray()
                            ray.departure_elevation, ray.departure_azimuth = departure
                            ray.arrival_elevation, ray.arrival_azimuth = arrival
                            ray.path_gain = path_gain
                            ray.time_of_arrival = arrival_time
                            ray.interactions = interactions_list
                            ray.phaseInDegrees = phases[rayIndex]
                            #add 1 because paths start from 1 instead of 0
                            ray.interactionsPositions = paths.get_interactions_positions_as_string(rec_i+1,rayIndex+1)

                            receiver.rays.append(ray)
                            rayIndex += 1 #update for next iteration
                            #print('Ray = ', ray.path_gain, ' ', ray.phaseInDegrees) #to check

                    object.receivers.append(receiver)
                    rec_i += 1
            scene.objects.append(object)

    episode.scenes.append(scene)
    print('\rProcessed episode: {} scene: {}, total {} '.format(ep_i, this_scene_i, sc_i + 1), end='')
    sc_i += 1
    this_scene_i += 1
    run_i += 1 #increment loop counter

print()
if episode == None:
    print('Warning: last episode == None')
else:
    session.add(episode)
    session.commit()
session.close()
print('Processed ', run_i, ' scenes (RT simulations)')
