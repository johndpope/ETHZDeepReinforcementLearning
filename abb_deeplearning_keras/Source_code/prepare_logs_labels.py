#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Thu Dec 22 11:37:27 2016

To build a HDF5 file for the image dataset

> read all the log files and combine them (D)
> clean up the columns (D)
> build a datetime index by combining the date and timestamps (D)
> add clearsky irradiation data from pvlib API (D)
> correct the timezones, daylight savings (D)
> normalize the timestamps b/w GHI and logs (D)
> add class labels (80% of GHI_threshold) 1min, 3min, 5min
> save it as HDF5, csv files (D)

Raw Logs Data format
---------------------
col 1: date dd-mm-yyyy
col 2: time HH:MM:SS
col 3: always 0
col 4: always 0
col 5: irradiation (W/m2) from horizontal sensor
col 6: irradiation (W/m2) from 45° north sensor
col 7: temperature measurement 1
col 8: temperature measurement 2

@author: maverick
"""

import h5py
import numpy as np
import tables
import pandas as pd
import os,sys, time
from datetime import datetime, timedelta, date
from dateutil import parser
from joblib import Parallel, delayed
import multiprocessing

#PVLIB Stuff
import itertools
import matplotlib.pyplot as plt
import pvlib
from pvlib import clearsky, atmosphere
from pvlib.location import Location


#merges multiple log files in a directory into a large dataframe
def merge_logs(source_dir):
    items = [ item for item in os.listdir( source_dir ) ]
    count = len(items)
    df = pd.DataFrame()
    for item in items:
        print item
        file_path = os.path.join(source_dir, item)
        item_df = read_log(file_path)
        df = df.append(item_df)
        print "remaining files "+ str(count)
        count -= 1
    df = df.sort_index()
    return df
    

# reads the log file and returns a pandas dataframe after cleanup
def read_log(source_file):
    col_names = ["date","time","col3","col4","irradiation_hs","irradiation_45","T1","T2"]
    df = pd.read_csv(source_file, sep=" ", header = None, names = col_names)
#    df = pd.read_csv(source_file, sep=" ", header = None)
#    df.columns = ["date", "time", "col3", "col4", "irradiation_hs", "irradiation_45", "T1", "T2"]
    df = df.drop("col3", axis=1)
    df = df.drop("col4", axis=1)
    # converts date, time to datetime object => timestamp    
    df['date'] = df['date'].apply(lambda val: datetime.strptime(val, '%d-%m-%Y'))
    df['time'] = df['time'].apply(lambda val: datetime.strptime(val,"%H:%M:%S").time())
    df['timestamp'] = df.apply(create_timestamp, axis=1)
    df.set_index('timestamp',inplace=True)
    return df


def create_timestamp(df):
    val = datetime.combine(df.date, df.time)
    return val

    

    
# method to loop over a date range and yields a set of dates
def date_range(start_date, end_date):
    for n in range(int ((end_date - start_date).days)):
        start = start_date
        end = start_date + timedelta(1)
        start_date = end
        yield {'start':start.strftime("%Y-%m-%d"), 'end': end.strftime("%Y-%m-%d") }


# retreives clearsky irradiance timeseries data for the given period        
#frequency = 1D, 1min, 5s, 5000ms    
#Timezones - CET, Europe/Zurich,    
def get_GHI_data(period):
    print period["start"] +","+ period["end"]
    tus = Location(43.5354, 11.4814, 'CET', 308, 'Cavriglia')
    times = pd.DatetimeIndex(start= period["start"], end= period["end"], freq='1s', tz=tus.tz)
    cs = tus.get_clearsky(times)  # ineichen with climatology table by default
    return cs
               

# to retrieve GHI data in parallel and concat them to form a larger data frame 
def build_irradiance_dataframe(start_date, end_date): #eg: format  'yyyy-mm-dd'

    start_date = parser.parse(start_date)
    end_date = parser.parse(end_date)
    
    ghi_list =Parallel(n_jobs=6)(delayed(get_GHI_data)(period) for period in date_range(start_date, end_date))
    ghi_data = pd.concat(ghi_list) #merges individual df's into one
    ghi_data = ghi_data.sort_index()
    
    return ghi_data


#Method offsets timestamps in the index (timestamp_wihout_timezone + hours added), Also truncates, few hours 
#worth of rows to make merging easier.    
def offset_time(df, hours):
    df.index.tz = None
    df.index = df.index + pd.DateOffset(hours=hours)
    df = df.head(-(hours*60*60))
    return df
    
#To verify timestamps and synchronize logs data, irradiance data
#Combines both timeseries dataframes built from the logs and pvlib API
def sync_timeseries(logs_data, ghi_data, plant='cavriglia'):
    if plant=='cavriglia':
        part1 = ghi_data.ix['2015-07-15':'2015-10-24']
        part2 = ghi_data.ix['2015-10-25':'2016-03-26']
        part3 = ghi_data.ix['2016-03-27':'2016-04-30']
        
        part1 = offset_time(part1, 2)
        part2 = offset_time(part2, 1)
        part3 = offset_time(part3, 2)
        
        frames = [part1, part2, part3]
        ghi_data = pd.concat(frames)
        
        ghi_data['dt'] = ghi_data.index
        logs_data['dt'] = logs_data.index
        master_data = pd.merge(logs_data, ghi_data, on='dt')
    #    master_data['timestamp'] = master_data['dt']
        master_data.set_index('dt',inplace=True)


#    ghi_data.index.tz = None  #'CET'
#    ghi_data.index = ghi_data.index + pd.DateOffset(hours=2)
#    ghi_data['dt'] = ghi_data.index
#    logs_data['dt'] = logs_data.index
#    master_data = pd.merge(logs_data, ghi_data, on='dt')
##    master_data['timestamp'] = master_data['dt']
#    master_data.set_index('dt',inplace=True)
    return master_data.sort_index()

    
#rolling window function to calculate irradiation change% (gradient based, measures against sensor data)
def calc_label_thresholds_gradient(row):
    try:
        x = store['irradiation_hs'].iloc[row['start_index']]
        y = store['irradiation_hs'].iloc[row['end_index']]
        
        if row['start_index'] % 10000 == 0:
            print row['start_index']
    except Exception,e:
        y = store['irradiation_hs'].iloc[row['end_index'] -1]
        print "index out of bound corrected"

    #appending 1 to zero values to avoid infinity, this makes x,y =1
    if(x==0):
        x+=1
        y+=1        
    metric = (((y/x)-1)*100)
    return metric


#rolling window function to calculate binary values for labels (gradient based, measures against irradiation_hs)
# 1=increase, 0=same, -1=decrease
def calc_label_binary_values_gradient(row):
    threshold_m = 6
    threshold_n = -6
    try:
        x = store['irradiation_hs'].iloc[row['start_index']]
        y = store['irradiation_hs'].iloc[row['end_index']]
        
        if row['start_index'] % 10000 == 0:
            print row['start_index']
    except Exception,e:
        y = store['irradiation_hs'].iloc[row['end_index'] -1]
        print "index out of bound corrected"
    
    #appending 1 to zero values to avoid infinity, this makes x,y =1
    if(x==0):
        x+=1
        y+=1
    metric = (((y/x)-1)*100)
    
    if  metric > threshold_m:
        return 1
    elif metric > threshold_n and metric < threshold_m:
        return 0
    elif metric < threshold_n:
        return -1

        
#rolling window function to calculate irradiation change% (measures against clearsky 'GHI' parameter)
def calc_label_thresholds_clear(row):
    clearsky = row['ghi']
    irradiation = row['irradiation_hs']
    threshold = 0.80 #70 %
    
    if (irradiation < (clearsky *threshold)):
        return 0 #occluded
    else:
        return 1 #clear

    
#rolling window function to calculate binary values for labels (measures against clearsky 'GHI' parameter)
# 1=increase, 0=same, -1=decrease
def calc_label_binary_values_clear(row):
    try:
        x = store['threshold'].iloc[row['start_index']]
        y = store['threshold'].iloc[row['end_index']]
        
        if row['start_index'] % 10000 == 0:
            print row['start_index']
    except Exception,e:
        y = store['threshold'].iloc[row['end_index'] -1]
        print "index out of bound corrected"

    if (x==0 and y==0): #occluded-occluded
        return 0
    elif (x==0 and y==1): #occluded-clear
        return 1
    elif (x==1 and y==0): #clear-occluded
        return 2
    elif (x==1 and y==1): #clear-clear
        return 3
        
        
        
# method to apply the thresholds and find the binary labels for a given duration
def process_labels(store, lookahead_duration):
    store = store.sort_index()
    store['dt'] = store.index
    start_dates = store['dt'] + pd.Timedelta(minutes = lookahead_duration)
    store['end_index'] = store['dt'].values.searchsorted(start_dates, side='right')
    store['start_index'] = np.arange(len(store))
#    store[str(lookahead_duration)+'_min_grad'] = store.apply(calc_label_thresholds_gradient, axis=1)
#    store[str(lookahead_duration)+'_bin_grad'] = store.apply(calc_label_binary_values_gradient, axis=1)
#    store['threshold'] = store.apply(calc_label_thresholds_clear, axis=1)
    store[str(lookahead_duration)+'_bin_clear'] = store.apply(calc_label_binary_values_clear, axis=1)
    return store

# Method to reduce the existing 4-label schema to 2-labels only, outputs only the future state
def compute_two_class_label(row):
    label_name = '5_bin_clear'
    if row[label_name] == 0:
        return 0 #occluded
    elif row[label_name] == 1:
        return 1 #clear
    elif row[label_name] == 2:
        return 0 #occluded
    elif row[label_name] == 3:
        return 1 #clear
    
#To calculate binary labels (0,1) only based on the future state. [00,01,10,11] reduced to [0,1]
meta_file = '/home/maverick/knet/out/master_index.h5'
store = pd.read_hdf(meta_file)
store['2_class'] = store.apply(lambda row: compute_two_class_label(row), axis =1)
len(store.loc[store['2_class'] == 0])
len(store.loc[store['2_class'] == 1])
store.to_hdf('out/master_index.h5','df',mode='w',format='table',data_columns=True)


####################################################    
source_dir = "/home/maverick/Desktop/temp/in/logs"
real = "/home/maverick/ABB_Dataset/Cavriglia_Logs"

#mdata = merge_logs(real)


start_time = time.time()
logs_series_data = merge_logs(real)
#ghi_series_data = build_irradiance_dataframe('2015-09-01', '2015-09-20')
ghi_series_data = pd.read_hdf('out/ghi_cavriglia.h5')
master_data = sync_timeseries(logs_series_data, ghi_series_data, plant='cavriglia')
    
print("--- %s seconds ---" % (time.time() - start_time))
copy = master_data


store = pd.read_hdf('out/merged_logs.h5')
store.ix['2015-07-15':'2015-07-17']
store = process_labels(store,3)
store = process_labels(store,5)
store = process_labels(store,7)
store = process_labels(store,10)
store.to_hdf('out/labels_logs.h5','df',mode='w',format='table',data_columns=True)




#To check no.of rows per label and balancing TEMP

len(store.loc[store['threshold'] == 1])
len(store.loc[store['threshold'] == 0])

len(store.loc[store['5_bin_clear'] == 0])
len(store.loc[store['5_bin_clear'] == 1])
len(store.loc[store['5_bin_clear'] == 2])
len(store.loc[store['5_bin_clear'] == 3])



len(store.loc[store['ghi'] == 0])
temp = store.ix['2015-07-15':'2015-07-16']
store.ix['2015-09-16':'2015-09-17'][['irradiation_hs','ghi']].plot()

store = master_data
store.ix['2015-11-26':'2015-11-26'][['irradiation_hs','ghi']].plot()

test=store.ix['2015-09-01':'2015-09-01'][['irradiation_hs','ghi']]

test.plot()



# Temp code for label analysis and verification
store = store.head(100000)
store = store.sort_index()
store['dt'] = store.index
#start_dates = store['dt'] - pd.Timedelta(minutes=5)
#store['start_index'] = store['dt'].values.searchsorted(start_dates, side='right')
#store['end_index'] = np.arange(len(store))
start_dates = store['dt'] + pd.Timedelta(minutes=1)
store['end_index'] = store['dt'].values.searchsorted(start_dates, side='right')
store['start_index'] = np.arange(len(store))


store['3_min']= store.apply(calc_label_thresholds, axis=1)

print(store[['dt', 'irradiation_45', '3_min','start_index','end_index']].iloc[100:150])


#To_HDF (with structure)
#*Time column is troubling with storing values as objects, type conversion could help*
copy.time = copy.time.astype('str')
#**
copy.to_hdf('out/merged_logs.h5','df',mode='w',format='table',data_columns=True)
store = pd.read_hdf('out/merged_logs.h5')
store.ix['2015-07-15':'2015-07-17']


#To_CSV (no structure)
copy.to_csv('out/merged.csv', sep=' ')
store = pd.read_csv('out/merged.csv', sep=' ')
store['dt'] = store['dt'].apply(lambda val: datetime.strptime(val, '%Y-%m-%d %H:%M:%S'))
store.set_index('dt', inplace=True)
store.ix['2015-07-15':'2015-07-17']


# Loading logs into Pandas and creating a H5 file out of it

source_file = "/home/maverick/Desktop/temp/in/logs/2015_11_25_data.log"
df = pd.read_csv(source_file, sep=" ", header = None, names = ["date","time","col3","col4","irradiation_hs","irradiation_45","T1","T2"])
#df.columns = ["date", "time", "col3", "col4", "irradiation_hs", "irradiation_45", "T1", "T2"]
df = df.drop("col3", axis=1)
df = df.drop("col4", axis=1)
df.to_hdf('test.h5','df',mode='w',format='table',data_columns=True)




#SUBSAMPLING - query samples

#for dates on index
mdata.ix['2015-07-15':'2015-07-17']
mdata['2015-07-15':'2015-07-17']

#for hours/min
hour = mdata.index.hour
selector = ((8 <= hour) & (hour <= 12)) | ((20 <= hour) & (hour <= 23))
data = mdata[selector]

#for dates on non-index columns
(mdata['date'] > '2015-07-15') & (mdata['date'] < '2015-07-18')




# Clearsky irradiance data for specific location
#frequency = 1D, 1min, 5s, 5000ms 
#latitude, longitude, tz, altitude, name = 32.2, -111, 'US/Arizona', 700, 'Tucson'

#tus = Location(43.5354, 11.4814, 'CET', 308, 'Cavriglia')
#times = pd.DatetimeIndex(start='2015-07-15', end='2015-07-16', freq='1min', tz=tus.tz)
#cs = tus.get_clearsky(times)  # ineichen with climatology table by default
#cs.plot()
#plt.ylabel('Irradiance $W/m^2$')
#plt.title('Ineichen, climatological turbidity')
