from pathlib import Path
import re
import os
import warnings
import logging
from time import perf_counter as timer
import sys
import json

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from tqdm import trange, tqdm
from scipy import stats
import pyarrow as pa
import pyarrow.csv as csv
import pyarrow.compute as pc
import xarray as xr

class fast_processing_engine():
    """This is a class to automatically process raw 10Hz files that have already been run through cardconvert
    
    ...
    
    Attributes
    ---------------
    site_info - dict of dict 
        contains all information for all sites relevant to the sites and time period of interest. Site information includes
            * fns - list of Path objects containing all found converted raw files of interest. Created by find_fast_files() method
            * file_tss - list of timestamps stored in those files' names. Created by find_fast_files() method
            * converted_path - Path to directory containing converted raw files. Provided by user
            * n_files_converted - int, len(fns). Created by find_fast_files() method
            * rawfile_metadata - pandas.DataFrame containing file metadata. Build during processing.
            * final_header - list of str giving the final header to be output to files
    start_time, end_time - timestamps 
        input by user to give the start and end points of the processing
    acq_freq - numeric
        user-input info on data acquisition frequency in Hz
    acq_period - pd.Timedelta
        acq_freq converted to time-domain
    file_length - int
        user-input raw file duration, Minutes
    n_records - int
        expected number of lines of data per file
    out_path - Path
        generated from user input. Gives the directory to write finished files to.
    desired_tss_start - list of timestamps
        the expected file name timestamps. Generated by find_fast_files() method
    
    Methods
    ---------------
    process_fast_files - does all the work of reading/processing/writing fast files in a standard format. 
      |--find_fast_files - searches for relevant raw data files and recovers their timestamps. Orders files by date.
      |    |--get_timestamp_from_fn - given a filename, return the timestamp
      |    |--get_fn_from_timestamp - given a timestamp and site, return the filename
      |--metadata_template - creates a template to populate with file metadata
      
      |--summary_template - generates a template for outputting summary data to
      |--process_interval - processes a single interval of data, across all sites
      |   |--process_file - reads in a single file and performs all the standardization operations.
      |   |    |--process_diagnostics - process turn diagnostic columns into useful information to give to eddypro
      |   |    |--reorder_headers - standardizes the file headers
      |   |--update_metadata - updates metadata for a single file
      |--update_summary - updates summary statistics for each site/timestamp
    make_empty - creates a blank fast file dataframe for the case that no valid files were found for a given time period. Used by multiple class methods
    """
    
    def __init__(self, converted_dirs, file_length, acq_freq, start_time, end_time, site_names, out_dir, tlog=True, ilog=True, blog=False):
        """
        converted_dirs - list of str
            directories containing raw converted TOA5 files. Each directory contains all the data to process from a given site, with no sub-directories. 
            For example, the following is an acceptable storage format:
                Converted/file1.dat
                Converted/file2.dat
            But the following is not accepted:
                Converted/2021/file1.dat
                Converted/2022/file2.dat
        file_length - int
            file length in minutes
        acq_freq - int
            acquisition speed in Hz
        start_time, end_time - str 
            the start and end dates for the files to process in <yyyy-mm-dd hh:MM> format. Round DOWN to the nearest half-hour.
        site_names - list of str
            site names to combine. If only one site is provided, then the data will be simply reformatted with consistent headers and regrouped into uniform intervals. If more than one is provided, then all provided data will be merged together as well (concatenated column-wise). Must be in the same order as converted_dirs. If
        out_dir - str
            desired output/processing directory.
        tlog, ilog, blog - bool
            whether or not to generate timing log info (output to file), general log info (output to stream), or debugging log info (output to stream)"""

        self.make_loggers(tlog, ilog, blog)
        
        # the final headers to use, grouped by site name.
        final_headers = {
            'NF17': [
                'TIMESTAMP',
                'RECORD',
                'Ux_CSAT3_NF17',
                'Uy_CSAT3_NF17',
                'Uz_CSAT3_NF17',
                'Ts_CSAT3_NF17',
                'Ux_CSAT3_NF7',
                'Uy_CSAT3_NF7',
                'Uz_CSAT3_NF7',
                'Ts_CSAT3_NF7',
                'CO2_LI7500_NF17',
                'H2O_LI7500_NF17',
                'PCELL_LI7500_NF17',
                'TCELL_LI7500_NF17',
                'DIAG_CSAT3_NF17',
                'flag_CSAT3_NF17',
                'DIAG_CSAT3_NF7',
                'flag_CSAT3_NF7',
                'DIAG_LI7500_NF17'
            ],
            'NF3': [
                'TIMESTAMP',
                'RECORD',
                'Ux_CSAT3B_NF3',
                'Uy_CSAT3B_NF3',
                'Uz_CSAT3B_NF3',
                'Ts_CSAT3B_NF3',
                'CO2_LI7500_NF3',
                'H2O_LI7500_NF3',
                'PCELL_LI7500_NF3',
                'TCELL_LI7500_NF3',
                'DIAG_CSAT3B_NF3',
                'flag_CSAT3B_NF3',
                'DIAG_LI7500_NF3'
            ],
            'SF4': [
                'TIMESTAMP',
                'RECORD',
                'Ux_SON_SF4',
                'Uy_SON_SF4',
                'Uz_SON_SF4',
                'Ts_SON_SF4',
                'CO2_IRGA_SF4',
                'H2O_IRGA_SF4',
                'PCELL_IRGA_SF4',
                'TCELL_IRGA_SF4',
                'DIAG_SON_SF4',
                'flag_SON_SF4',
                'DIAG_IRGA_SF4'
            ],
            'SF7': [
                'TIMESTAMP',
                'RECORD',
                'Ux_CSAT3B_SF7',
                'Uy_CSAT3B_SF7',
                'Uz_CSAT3B_SF7',
                'Ts_CSAT3B_SF7',
                'CO2_LI7500_SF7',
                'H2O_LI7500_SF7',
                'PCELL_LI7500_SF7',
                'TCELL_LI7500_SF7',
                'DIAG_CSAT3B_SF7',
                'flag_CSAT3B_SF7',
                'DIAG_LI7500_SF7'
            ],
            'UF3': [
                'TIMESTAMP',
                'RECORD',
                'Ux_SON_UF3',
                'Uy_SON_UF3',
                'Uz_SON_UF3',
                'Ts_SON_UF3',
                'CO2_IRGA_UF3',
                'H2O_IRGA_UF3',
                'PCELL_IRGA_UF3',
                'TCELL_IRGA_UF3',
                'DIAG_SON_UF3',
                'flag_SON_UF3',
                'DIAG_IRGA_UF3'
            ]
        }
        
        # all site-specific metadata is stored here
        self.site_info = {site:{"fns":None, 
                                "file_tss":None, 
                                "converted_path":Path(converted_dir), 
                                "n_files_converted":None,
                                "rawfile_metadata":None,
                                "final_header":final_headers[site]
                               }
                         for site, converted_dir in zip(site_names, converted_dirs)}
        
        # convert user-provided start/end times
        self.start_time = pd.to_datetime(start_time)
        self.end_time = pd.to_datetime(end_time)

        # individual file information
        # convert acq freq to an interval for better compatibility with time deltas
        self.acq_freq = acq_freq
        self.acq_period = pd.Timedelta(f'{1000//self.acq_freq} ms')
        self.file_length = file_length
        self.n_records = self.file_length*self.acq_freq*60

        # convert files and directories to path objects and create an output directory
        self.out_path = Path(out_dir)
        
        for cdir in converted_dirs:
            if not Path(cdir).exists():
                self.ilogger.error('Converted files directory not found. Make sure you are connected to the file server.')
                sys.exit(1)
        
        if not self.out_path.exists():
            should_make_outpath = input(f'Looks like {out_dir} doesn\'t exist. Should I create it? (y/n)')
            if should_make_outpath == 'y':
                self.out_path.mkdir(parents=True)
            else: 
                self.ilogger.error('Output directory not created. Exiting...')
                sys.exit(1)
        return
    
    def make_loggers(self, tlog, ilog, blog):
        """creates loggers. tlog, ilog, blog toggle the timer, informational, and debugging loggers."""
        # time logger
        tfmt = logging.Formatter('%(message)s')
        if Path('time.log').exists():
            print('exists')
            Path('time.log').unlink()
            with open('time.log', 'w') as f: pass
        thdl = logging.FileHandler('./time.log')
        thdl.setFormatter(tfmt)
        if tlog:
            thdl.setLevel(logging.INFO)
        else:
            thdl.setLevel(logging.CRITICAL)
        
        
        # general user info logger
        ifmt = logging.Formatter('%(levelname)s    %(lineno)d    %(message)s')
        ihdl = logging.StreamHandler()
        ihdl.setFormatter(ifmt)
        if ilog:
            ihdl.setLevel(logging.INFO)
        else:
            ihdl.setLevel(logging.CRITICAL)
        
        # debug logger
        bfmt = logging.Formatter('%(levelname)s    %(lineno)d    %(message)s')
        bhdl = logging.StreamHandler()
        bhdl.setFormatter(bfmt)
        if blog:
            bhdl.setLevel(logging.DEBUG)
        else:
            bhdl.setLevel(logging.CRITICAL)
        
        self.tlogger = logging.getLogger(__name__ + 'timer')
        self.tlogger.addHandler(thdl)
        self.tlogger.setLevel(logging.DEBUG)
        
        self.ilogger = logging.getLogger(__name__ + 'info')
        self.ilogger.addHandler(ihdl)
        self.ilogger.setLevel(logging.DEBUG)
        
        self.blogger = logging.getLogger(__name__ + 'debugger')
        self.blogger.addHandler(bhdl)
        self.blogger.setLevel(logging.DEBUG)
        
        
        self.blogger.debug('Created loggers')
        
    def process_fast_files(self):
        """reads in raw fast files, and combines/standardizes them to be continuous."""
        
        # sanity check
        print("Processing data from...")
        for site in self.site_info:
            print(f"Site {site} in {self.site_info[site]['converted_path']}")
        print("Outputting data to...")
        print(self.out_path)
        input("Press Enter to confirm")
        
        # locate the files we're interested in
        t0 = timer()
        self.find_fast_files()
        self.tlogger.info(f'Found fast files in {1000*(timer() - t0)}ms')
        
        # create a template for summary data, dims (time, stats, site, )
        summary_cols = {'Ux':0, 'Uy':1, 'Uz':2, 'Ts':3, 'CO2':4, 'H2O':5, 'PCELL':6, 'TCELL':7, 'flag':8}
        summary_sites = {site:i for i, site in enumerate(self.site_info)}
        # edge case
        if 'NF17' in summary_sites:
            summary_sites['NF7'] = max(summary_sites.values()) + 1
        summary_arr = np.full(shape=(len(self.desired_file_tss), 5, len(summary_sites), len(summary_cols)),
                             fill_value = np.nan)
        
        # initialize the file metadata template
        self.metadata_template()
        # appending to dataframes is slow and inneficient. 
        # Keeping track of the number files seen separate from the number of timestamps seen helps us to fill in a pre-made table,
        # which is much faster
        ifile = {site:0 for site in self.site_info}
        
        # we'll be popping file names off of fns and file_tss, so let's create temporary copies of 
        # them first to avoid destroying the originals
        for site in self.site_info:
            self.site_info[site]['fns_temp'] = self.site_info[site]['fns'].copy()
            self.site_info[site]['file_tss_temp'] = self.site_info[site]['file_tss'].copy()
        
        # loop through OUTPUT file timestamps/names
        pbar = tqdm(self.desired_file_tss)
        for idfts, dfts in enumerate(pbar):
            pbar.set_description(f'Processing {dfts}')
            
#             # prep the output file: 
#             # generate the desired time index for that file
#             tloop = timer()
#             ti = timer()
#             desired_time_index = pd.date_range(dfts + self.acq_period, periods=self.n_records, freq=self.acq_period)
#             dat = pd.DataFrame(desired_time_index, columns=['TIMESTAMP'])
#             dat.set_index('TIMESTAMP', inplace=True)
#             self.tlogger.info(f'Built:{1000*(timer() - ti):.4f}')
            
#             # generate output file name:
#             # yyyy-mm-dd hh:MM --> yyyy_mm_dd_hh:MM --> yyyy_mm_dd_hhMM
#             dfts_str = re.sub('-| ', '_', str(dfts))
#             dfts_str = re.sub(':', '', dfts_str)[:-2]
#             desired_fn = self.out_path / f'{dfts_str}.csv'
            
#             # now loop through each site at this timestamp. For each site, search for all qualified files for this timestamp.
#             for site in self.site_info:
#                 ti = timer()
#                 next_file_ts = dfts + pd.Timedelta(f'{self.file_length} Min')
#                 next_fns, next_file_tss = [], []
#                 try:
#                     while self.site_info[site]['file_tss_temp'][0] < next_file_ts:
#                         next_fns.append(self.site_info[site]['fns_temp'].pop(0))
#                         next_file_tss.append(self.site_info[site]['file_tss_temp'].pop(0))
#                 # when the temp lists empty, we'll get an indexerror
#                 except IndexError as err:
#                     pass
#                 self.tlogger.info(f'Next:{1000*(timer() - ti):.4f}')
                
                
#                 # combine qualified files into one dataframe: 
#                 # apply header changes, calibrations, raw data corrections, record file metadata, etc
#                 ti = timer()
#                 # if none are found, make an empty dataframe
#                 if next_fns == []:
#                     rawdat = self.make_empty(dfts, site)
#                     rawdat = rawdat.loc[:, rawdat.columns != 'RECORD']
#                     self.tlogger.info(f'No data file to read in')
#                     self.tlogger.info(f'Made empty datafile in {1000*(timer() - ti):.4f}ms')
#                 else:
#                     for i, fn, ts in zip(range(len(next_fns)), next_fns, next_file_tss):
#                         # combine files
#                         if i == 0:
#                             rawdat = self.process_file(fn, site, ts)
#                         else:
#                             rawdat_tmp = self.process_file(fn, site, ts)
#                             rawdat = pd.concat([rawdat, rawdat_tmp])
#                         # record metadata
#                         with open(fn) as f:
#                             metadata_row = [dfts, desired_fn, ts, fn] + f.readline()[1:-1].split('","')
#                             self.site_info[site]['rawfile_metadata'].iloc[ifile[site]] = metadata_row
#                         ifile[site] += 1  
                                
#                     self.tlogger.info(f'Processed:{1000*(timer() - ti)}')

#                 # now combine merge files across sites
#                 dat = dat.merge(rawdat, how='outer', left_index=True, right_index=True, sort=True)
            
#             # write the final data file to a csv using PyArrow
#             ti = timer()
#             pa_table = csv.write_csv(
#                 pa.Table.from_pandas(dat[dat.columns[1:]], 
#                                      preserve_index=False, 
#                                      nthreads=4, 
#                                      schema=pa.schema([pa.field(colname, pa.float32()) for colname in dat.columns[1:]])),
#                 desired_fn,
#             )
#             
#             self.tlogger.info(f'Wrote:{1000*(timer() - ti):.4f} Disk:{os.stat(desired_fn).st_size/1e6:.4f} Memory:{dat.memory_usage().sum()/1e6:.4f}')
#             self.tlogger.info(f'Comleted:{1000*(timer() - tloop)}')

            dat = self.process_interval(idfts, dfts, ifile)
            
            # write summary stats for analysis and QA/QC
            ti = timer()
            summary_arr = self.update_summary(dat, summary_arr, summary_cols, summary_sites, idfts, site)
            self.tlogger.info(f'Summary:{1000*(timer() - ti):.4f}')
                        
        # after whole run is complete: convert summary stats to an xarray    
        self.summary = xr.Dataset(
            data_vars={
                colname:(['TIMESTAMP', 'STAT', 'SITE'], summary_arr[:, :, :, icolname]) 
                for icolname, colname in enumerate(summary_cols)
            },
            coords={
                'TIMESTAMP':self.desired_file_tss,
                'STAT':['Avg', 'Max', 'Min', 'Std', 'Npc'],
                'SITE':list(summary_sites.keys())
            }
        )
        
        self.tlogger.info(f'Run complete. Processed {ifile} files across {len(self.site_info)} sites in {timer() - t0}s')
        
        return
    
    def process_interval(self, idfts, dfts, ifile):
        '''processes one timestamp worth of data across multiple sites. Reads in one timestamp worth of data, outputs one timestamp worth of data, and returns metadata and raw output data'''

        # prep the output file: 
        # generate the desired time index for that file
        tloop = timer()
        ti = timer()
        desired_time_index = pd.date_range(dfts + self.acq_period, periods=self.n_records, freq=self.acq_period)
        dat = pd.DataFrame(desired_time_index, columns=['TIMESTAMP'])
        dat.set_index('TIMESTAMP', inplace=True)
        self.tlogger.info(f'Built:{1000*(timer() - ti):.4f}')

        # generate output file name:
        # yyyy-mm-dd hh:MM --> yyyy_mm_dd_hh:MM --> yyyy_mm_dd_hhMM
        dfts_str = re.sub('-| ', '_', str(dfts))
        dfts_str = re.sub(':', '', dfts_str)[:-2]
        desired_fn = self.out_path / f'{dfts_str}.csv'

        # now loop through each site at this timestamp. For each site, search for all qualified files for this timestamp.
        for site in self.site_info:
            ti = timer()
            next_file_ts = dfts + pd.Timedelta(f'{self.file_length} Min')
            next_fns, next_file_tss = [], []
            try:
                while self.site_info[site]['file_tss_temp'][0] < next_file_ts:
                    next_fns.append(self.site_info[site]['fns_temp'].pop(0))
                    next_file_tss.append(self.site_info[site]['file_tss_temp'].pop(0))
            # when the temp lists empty, we'll get an indexerror
            except IndexError as err:
                pass
            self.tlogger.info(f'Next:{1000*(timer() - ti):.4f}')


            # combine qualified files into one dataframe: 
            # apply header changes, calibrations, raw data corrections, record file metadata, etc
            ti = timer()
            # if none are found, make an empty dataframe
            if next_fns == []:
                rawdat = self.make_empty(dfts, site)
                rawdat = rawdat.loc[:, rawdat.columns != 'RECORD']
                self.tlogger.info(f'No data file to read in')
                self.tlogger.info(f'Made empty datafile in {1000*(timer() - ti):.4f}ms')
            else:
                for i, fn, ts in zip(range(len(next_fns)), next_fns, next_file_tss):
                    # combine files
                    if i == 0:
                        rawdat = self.process_file(fn, site, ts)
                    else:
                        rawdat_tmp = self.process_file(fn, site, ts)
                        rawdat = pd.concat([rawdat, rawdat_tmp])
                    # record metadata
                    ifile = self.update_metadata(dfts, desired_fn, ts, fn, site, ifile)
                    
                self.tlogger.info(f'Processed:{1000*(timer() - ti)}')

            # now combine merge files across sites
            dat = dat.merge(rawdat, how='outer', left_index=True, right_index=True, sort=True)

        # write the final data file to a csv using PyArrow
        ti = timer()
        pa_table = csv.write_csv(
            pa.Table.from_pandas(dat[dat.columns[1:]], 
                                 preserve_index=False, 
                                 nthreads=4, 
                                 schema=pa.schema([pa.field(colname, pa.float32()) for colname in dat.columns[1:]])),
            desired_fn,
        )
        
        self.tlogger.info(f'Wrote:{1000*(timer() - ti):.4f} Disk:{os.stat(desired_fn).st_size/1e6:.4f} Memory:{dat.memory_usage().sum()/1e6:.4f}')
        self.tlogger.info(f'Comleted:{1000*(timer() - tloop)}')
        
        return dat

    def find_fast_files(self):
        """find all the raw data files that the user wants to process and place them in the site info dict, ordered by timestamp."""
        
        # get the timestamps we want create, which may not align with the raw file timestamps
        self.desired_file_tss = pd.date_range(self.start_time, self.end_time, freq=f'{self.file_length} min')
        self.ilogger.info(f'searching for files in range {self.desired_file_tss[0]}...{self.desired_file_tss[-1]}')
        for site in self.site_info:
            # get the actual file names
            self.site_info[site]['fns'] = list(self.site_info[site]['converted_path'].glob('TOA5*.dat'))
            
            self.ilogger.info(f"Found {len(self.site_info[site]['fns'])} files for site {site}:")
            
            if len(self.site_info[site]['fns']):
                self.ilogger.info(self.site_info[site]['fns'][0].name + "  ...  " + self.site_info[site]['fns'][-1].name)
            
            # get raw file timestamps from the raw file names
            file_tss = []
            for fn in self.site_info[site]['fns']:
                fts = self.get_timestamp_from_fn(fn)
                if (fts >= self.start_time and fts <= self.end_time):
                    file_tss.append(self.get_timestamp_from_fn(fn))
                
            # sort file names by timestamp
            file_tss = sorted(file_tss)
            self.site_info[site]['fns'] = [self.get_fn_from_timestamp(fts, site) for fts in file_tss]
            self.site_info[site]['file_tss'] = file_tss.copy()
            
            # also record the number of total files we converted
            self.site_info[site]['n_files_converted'] = len(file_tss)
                                     
    def get_timestamp_from_fn(self, fn):
        """given a raw, converted file, this will extract the timestamp given in its name. Files are expected to be of the format TOA5*Hz*_yyyy_mm_dd_hh_MM.dat"""
        file_id = Path(fn).name.split('Hz')[1]
        file_start_str = "".join(re.split("_|\.", file_id)[1:-1])
        file_start_ts = pd.to_datetime(file_start_str, format="%Y%m%d%H%M")
        return file_start_ts
    
    def get_fn_from_timestamp(self, file_start_ts, site):
        """given a timestamp, this will find the exact file name it's associated with"""
        file_start_str = (f'{file_start_ts.year:04d}_' + 
                          f'{file_start_ts.month:02d}_' + 
                          f'{file_start_ts.day:02d}_' + 
                          f'{file_start_ts.hour:02d}{file_start_ts.minute:02d}')

        # if the file exists
        
        for i, fn in enumerate(self.site_info[site]['fns']):
            if file_start_str in str(fn):
                return fn

        # if not
        return
    
    def process_file(self, fn, site, ts):
        """read in a single raw file and return a formatted version"""
        # read in the file. Rows 0, 2, and 3 contain boring metadata. After rows 0, 2, 3 are removed, the new row 0 is the true header
        
        ti = timer()
        rawdat = csv.read_csv(
            fn, 
            parse_options=csv.ParseOptions(delimiter=','),
            convert_options=csv.ConvertOptions(null_values=['"NAN"', '-4400906', '-9999']),
            read_options=csv.ReadOptions(use_threads=True, skip_rows=1, skip_rows_after_names=2)
        ).to_pandas(use_threads=True)
        self.tlogger.info(f'Read:{1000*(timer() - ti):.4f} Disk:{os.stat(fn).st_size/1e6:.4f} Memory:{rawdat.memory_usage().sum()/1e6:.4f}')
        
        rawdat['TIMESTAMP'] = pd.to_datetime(rawdat['TIMESTAMP'], format='%Y-%m-%d %H:%M:%S.%f')
        
        # add diagnostic flags
        rawdat = self.process_diagnostics(rawdat, site)
        # standardize header
        rawdat = self.reorder_headers(site, ts, rawdat)
        rawdat.set_index('TIMESTAMP', inplace=True)
        rawdat = rawdat.loc[:, rawdat.columns != 'RECORD']
        
        return rawdat
    
    def process_diagnostics(self, df, site):
        """search a dataframe for diagnostic columns and turn them into boolean flags"""
        # how to process diagnostic bits for each instrument
        diag_dict = {
            'CSAT3': {f'flag_CSAT3_{site}': lambda x: int(bool(x >> 12))},
            'CSAT3B': {f'flag_CSAT3B_{site}': lambda x: int(bool(x & 0b101011111))},
            'IRGA': {f'flag_IRGA_{site}': lambda x: int(bool(x))},
            'SON': {f'flag_SON_{site}': lambda x: int(bool(x))},
#             'LI7500': {f'flag_LI7500_{site}': lambda x: int(bool(x>>4 ^ 0b1111))}
        }
        
        # search all columns in dataframe
        for colname in df.columns:
            # check to see if this column contains diagnostics
            instr = re.search("DIAG_CSAT3_|DIAG_CSAT3B|DIAG_IRGA|DIAG_SON", colname)
            if instr: 
                # truncate regex match to remove the "DIAG_" at the front
                instr = instr[0][5:]
                # CSAT3_ matches strangely, so manually rename this one
                if instr == 'CSAT3_': 
                    instr = 'CSAT3'
                # write diagnostic column: for example, if we found the CSAT3 diagnostic column for NF17, compute the following:
                # df = df.assign(flag_CSAT3_NF17=lambda x: int(bool(x >> 12)))
                df = df.assign(**diag_dict[instr])
                pd.DataFrame.assign()
        return df  
    
    def reorder_headers(self, site, fts, df):
        """Data headers change over time as the site evolves. This method will rearrange the dataframe to fit into a standard-order header, given the site name and date. All such metadata is input manually. Refer to self.site_info[site]['header_metadat'] for help crafting these.
        
        steps:
            1. Identify the date/site
            3. Record the missing columns and the renaming scheme
            4. Add the missing columns
            5. Apply renaming scheme
            6. Alphabetize columns"""
        
        if site == 'NF17':
            if fts.date() < pd.to_datetime("2019-05-19"):
                cols_to_add = ["PCELL_LI7500_NF17", "DIAG_CSAT3_NF17", "DIAG_CSAT3_NF7", "flag_CSAT3_NF17", "flag_CSAT3_NF7", 'TCELL_LI7500_NF17']
                renaming_dict = {
                     'Ux_CSAT3_17m':"Ux_CSAT3_NF17",
                     'Uy_CSAT3_17m':"Uy_CSAT3_NF17",
                     'Uz_CSAT3_17m':"Uz_CSAT3_NF17",
                     'Ts_CSAT3_17m':"Ts_CSAT3_NF17",
                     'Ux_CSAT3_7m':"Ux_CSAT3_NF7",
                     'Uy_CSAT3_7m':"Uy_CSAT3_NF7",
                     'Uz_CSAT3_7m':"Uz_CSAT3_NF7",
                     'Ts_CSAT3_7m':"Ts_CSAT3_NF7",
                     'rho_c_LI7500':"CO2_LI7500_NF17",
                     'rho_v_LI7500':"H2O_LI7500_NF17",
                     'DIAG_LI7500':"DIAG_LI7500_NF17"
                }
                
            # differences: P_LI7500 was added
            elif fts.date() > pd.to_datetime("2019-05-19"):
                cols_to_add = ["DIAG_CSAT3_NF17", "DIAG_CSAT3_NF7", "flag_CSAT3_NF17", "flag_CSAT3_NF7", 'TCELL_LI7500_NF17']
                renaming_dict = {
                     'Ux_CSAT3_17m':"Ux_CSAT3_NF17",
                     'Uy_CSAT3_17m':"Uy_CSAT3_NF17",
                     'Uz_CSAT3_17m':"Uz_CSAT3_NF17",
                     'Ts_CSAT3_17m':"Ts_CSAT3_NF17",
                     'Ux_CSAT3_7m':"Ux_CSAT3_NF7",
                     'Uy_CSAT3_7m':"Uy_CSAT3_NF7",
                     'Uz_CSAT3_7m':"Uz_CSAT3_NF7",
                     'Ts_CSAT3_7m':"Ts_CSAT3_NF7",
                     'rho_c_LI7500':"CO2_LI7500_NF17",
                     'rho_v_LI7500':"H2O_LI7500_NF17",
                     "P_LI7500":"PCELL_LI7500_NF17",
                     'DIAG_LI7500':"DIAG_LI7500_NF17"
                }
                
            # if the file ever overlaps with a "maintenance" day, omit that whole day
            elif fts.date() == pd.to_datetime("2019-05-19"):
                df = self.make_empty(fts, site)
                return df
                
        elif site == "NF3":
            if fts.date() < pd.to_datetime("2019-05-19"):
                cols_to_add = ["PCELL_LI7500_NF3", 'TCELL_LI7500_NF3', 'flag_CSAT3B_NF3']
                renaming_dict = {
                     'Ux_CSAT3B':"Ux_CSAT3B_NF3",
                     'Uy_CSAT3B':"Uy_CSAT3B_NF3",
                     'Uz_CSAT3B':"Uz_CSAT3B_NF3",
                     'Ts_CSAT3B':"Ts_CSAT3B_NF3",
                     'rho_c_LI7500':"CO2_LI7500_NF3",
                     'rho_cv_LI7500':"H2O_LI7500_NF3",
                     'DIAG_CSAT3B':"DIAG_CSAT3B_NF3",
                     'DIAG_LI7500':"DIAG_LI7500_NF3"
                }
            
            # differences: diag_csat3b removed, p_li7500 added
            elif fts.date() > pd.to_datetime("2019-05-19"):
                cols_to_add = ["DIAG_CSAT3B_NF3", "flag_CSAT3B_NF3", 'TCELL_LI7500_NF3']
                renaming_dict = {
                     'Ux_CSAT3B':"Ux_CSAT3B_NF3",
                     'Uy_CSAT3B':"Uy_CSAT3B_NF3",
                     'Uz_CSAT3B':"Uz_CSAT3B_NF3",
                     'Ts_CSAT3B':"Ts_CSAT3B_NF3",
                     'rho_c_LI7500':"CO2_LI7500_NF3",
                     'rho_v_LI7500':"H2O_LI7500_NF3",
                     'P_LI7500':'PCELL_LI7500_NF3',
                     'DIAG_LI7500':"DIAG_LI7500_NF3"
                }
            
            elif fts.date() == pd.to_datetime("2019-05-19"):
                df = self.make_empty(fts, site)
                return df
                
        elif site == "SF4":
            if fts.date() < pd.to_datetime("2100-11-19"):
                cols_to_add = ['flag_SON_SF4']
                renaming_dict = {
                    'Ux': 'Ux_SON_SF4',
                    'Uy': 'Uy_SON_SF4',
                    'Uz': 'Uz_SON_SF4',
                    'Ts': 'Ts_SON_SF4',
                    'diag_sonic': 'DIAG_SON_SF4',
                    'CO2': 'CO2_IRGA_SF4',
                    'H2O': 'H2O_IRGA_SF4',
                    'diag_irga': 'DIAG_IRGA_SF4',
                    'cell_tmpr': 'TCELL_IRGA_SF4',
                    'cell_press': 'PCELL_IRGA_SF4'
                }   
            
        elif site == 'SF7':
            if fts.date() < pd.to_datetime("2019-05-19"):
                cols_to_add = ["PCELL_LI7500_SF7", 'TCELL_LI7500_SF7', 'flag_CSAT3B_SF7']
                renaming_dict = {
                     'Ux_CSAT3B':"Ux_CSAT3B_SF7",
                     'Uy_CSAT3B':"Uy_CSAT3B_SF7",
                     'Uz_CSAT3B':"Uz_CSAT3B_SF7",
                     'Ts_CSAT3B':"Ts_CSAT3B_SF7",
                     'rho_c_LI7500':"CO2_LI7500_SF7",
                     'rho_v_LI7500':"H2O_LI7500_SF7",
                     'DIAG_CSAT3B':"DIAG_CSAT3B_SF7",
                     'DIAG_LI7500':"DIAG_LI7500_SF7"
                }
                
            elif fts.date() > pd.to_datetime("2019-05-19"):
                cols_to_add = ["DIAG_CSAT3B_SF7", "flag_CSAT3B_SF7", 'TCELL_LI7500_SF7']
                renaming_dict = {
                     'Ux_CSAT3B':"Ux_CSAT3B_SF7",
                     'Uy_CSAT3B':"Uy_CSAT3B_SF7",
                     'Uz_CSAT3B':"Uz_CSAT3B_SF7",
                     'Ts_CSAT3B':"Ts_CSAT3B_SF7",
                     'rho_c_LI7500':"CO2_LI7500_SF7",
                     'rho_v_LI7500':"H2O_LI7500_SF7",
                     'P_LI7500':'PCELL_LI7500_SF7',
                     'DIAG_LI7500':"DIAG_LI7500_SF7"
                }
            
            elif fts.date() == pd.to_datetime("2019-05-19"):
                df = self.make_empty(fts, site)
                return df
        elif site == 'UF3': 
            if fts.date() > pd.to_datetime("2019-02-13"):
                    cols_to_add = ['flag_SON_UF3']
                    renaming_dict = {
                        'Ux': 'Ux_SON_UF3',
                        'Uy': 'Uy_SON_UF3',
                        'Uz': 'Uz_SON_UF3',
                        'Ts': 'Ts_SON_UF3',
                        'diag_sonic': 'DIAG_SON_UF3',
                        'CO2': 'CO2_IRGA_UF3',
                        'H2O': 'H2O_IRGA_UF3',
                        'diag_irga': 'DIAG_IRGA_UF3',
                        'cell_tmpr': 'TCELL_IRGA_UF3',
                        'cell_press': 'PCELL_IRGA_UF3'
                    }
        
        new_order = self.site_info[site]['final_header']
        # apply new column names/order to the data
        # first, add columns
        df = df.reindex(columns=list(df.columns) + cols_to_add, fill_value=np.nan)  # add missing cols
        df.rename(columns=renaming_dict, inplace=True)  # rename to final values
        df = df[new_order]  # re-order and select only desired columns
        
        return df 
    
    def update_summary(self, dat, summary_arr, summary_cols, summary_sites, idfts, site):
        """compute summary statistics for current timestamp and site"""
        
        with warnings.catch_warnings():
                    warnings.simplefilter("ignore", category=RuntimeWarning)
                    for icolname, colname in enumerate(dat.columns):
                        if colname[0] in 'UTCHPF':  #check that this col is one of Ux/y/z, Ts/TCELL, CO2, H2O, PCELL, flag
                        # place stats into position (time, :, site, var)
                            site = colname.split('_')[-1]
                            col_id = colname.split('_')[0]
                            summary_arr[idfts, :, summary_sites[site], summary_cols[col_id]] = [
                                np.nanmean(dat[colname]),
                                np.nanmax(dat[colname]),
                                np.nanmin(dat[colname]),
                                np.nanstd(dat[colname]),
                                100 - 100*np.nansum(0*dat[colname] + 1)/self.n_records
                            ]
        return summary_arr
    
    def make_empty(self, fts, site):
            """make an empty fast file to indicate missing data. Creates a dataframe with two entries in the given time period with the proper header, populated by nans.
            
            Example:
            TIMESTAMP             | Ux_CSAT3B_NF3 | Uy_CSAT3B_NF3 | ... | DIAG_LI7500_NF3
            ------------------------------------------------------------------------------
            2021-05-05 12:00:00.1 | NaN           | NaN           | ... | NaN
            2021-05-05 12:00:00.2 | NaN           | NaN           | ... | NaN
            """
            emptydf = (pd.DataFrame({'TIMESTAMP':pd.date_range(start=fts + self.acq_period, freq="100ms", periods=2)})
                    .reindex(columns=self.site_info[site]['final_header'], fill_value = np.nan)
                    .set_index('TIMESTAMP'))
            return emptydf
      
    def metadata_template(self):
        """create a blank template to store raw TOA5 file metadata in. This can be referenced later for debugging purposes"""
        
        for site in self.site_info:
            self.site_info[site]['rawfile_metadata'] = pd.DataFrame(
                [['none']*12]*self.site_info[site]['n_files_converted'],  # number of rows
                columns = [
                    'output_timestamp', 
                    'output_name',
                    'input_timestamp', 
                    'input_name', 
                    'Encoding', 
                    'Station_name', 
                    'Datalogger_model', 
                    'Datalogger_serial_number', 
                    'Datalogger_OS_version', 
                    'Datalogger_program_name', 
                    'Datalogger_program_signature', 
                    'Table_name'
                ]
            )
        return  
    
    def update_metadata(self, dfts, desired_fn, ts, fn, site, ifile):
        with open(fn) as f:
            metadata_row = [dfts, desired_fn, ts, fn] + f.readline()[1:-1].split('","')
            self.site_info[site]['rawfile_metadata'].iloc[ifile[site]] = metadata_row
        ifile[site] += 1  

        return ifile