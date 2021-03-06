#!"D:\curw_flo2d_data_manager\venv\Scripts\python.exe"

import json
import traceback
import sys
import os
from datetime import datetime, timedelta
import re
import getopt
import time

from db_adapter.logger import logger
from db_adapter.constants import set_db_config_file_path
from db_adapter.constants import connection as con_params
# from db_adapter.constants import COMMON_DATE_TIME_FORMAT, CURW_FCST_DATABASE, CURW_FCST_PASSWORD, CURW_FCST_USERNAME, \
#     CURW_FCST_PORT, CURW_FCST_HOST
from db_adapter.constants import COMMON_DATE_TIME_FORMAT
from db_adapter.base import get_Pool
from db_adapter.curw_fcst.source import get_source_id, get_source_parameters
from db_adapter.curw_fcst.variable import get_variable_id
from db_adapter.curw_fcst.unit import get_unit_id, UnitType
from db_adapter.curw_fcst.station import get_flo2d_output_stations, StationEnum
from db_adapter.curw_fcst.timeseries import Timeseries
from db_adapter.curw_fcst.timeseries import insert_run_metadata

ROOT_DIRECTORY = 'D:\curw_flo2d_data_manager'
flo2d_stations = { }


def read_attribute_from_config_file(attribute, config, compulsory):
    """
    :param attribute: key name of the config json file
    :param config: loaded json file
    :param compulsory: Boolean value: whether the attribute is must present or not in the config file
    :return:
    """
    if attribute in config and (config[attribute]!=""):
        return config[attribute]
    elif compulsory:
        logger.error("{} not specified in config file.".format(attribute))
        exit(1)
    else:
        logger.error("{} not specified in config file.".format(attribute))
        return None


def get_file_last_modified_time(file_path):
    # returns local time (UTC + 5 30)

    modified_time_raw = os.path.getmtime(file_path)
    modified_time_utc = datetime.utcfromtimestamp(modified_time_raw)
    modified_time_SL = modified_time_utc + timedelta(hours=5, minutes=30)

    return modified_time_SL.strftime('%Y-%m-%d %H:%M:%S')


def check_time_format(time):
    try:
        time = datetime.strptime(time, COMMON_DATE_TIME_FORMAT)

        if time.strftime('%S') != '00':
            print("Seconds should be always 00")
            exit(1)
        if time.strftime('%M') not in ('00', '15', '30', '45'):
            print("Minutes should be always multiples of 15")
            exit(1)

        return time
    except Exception:
        print("Time {} is not in proper format".format(time))
        exit(1)


def getUTCOffset(utcOffset, default=False):
    """
    Get timedelta instance of given UTC offset string.
    E.g. Given UTC offset string '+05:30' will return
    datetime.timedelta(hours=5, minutes=30))

    :param string utcOffset: UTC offset in format of [+/1][HH]:[MM]
    :param boolean default: If True then return 00:00 time offset on invalid format.
    Otherwise return False on invalid format.
    """
    offset_pattern = re.compile("[+-]\d\d:\d\d")
    match = offset_pattern.match(utcOffset)
    if match:
        utcOffset = match.group()
    else:
        if default:
            print("UTC_OFFSET :", utcOffset, " not in correct format. Using +00:00")
            return timedelta()
        else:
            return False

    if utcOffset[0]=="+":  # If timestamp in positive zone, add it to current time
        offset_str = utcOffset[1:].split(':')
        return timedelta(hours=int(offset_str[0]), minutes=int(offset_str[1]))
    if utcOffset[0]=="-":  # If timestamp in negative zone, deduct it from current time
        offset_str = utcOffset[1:].split(':')
        return timedelta(hours=-1 * int(offset_str[0]), minutes=-1 * int(offset_str[1]))


def get_water_level_of_channels(lines, channels=None):
    """
     Get Water Levels of given set of channels
    :param lines:
    :param channels:
    :return:
    """
    if channels is None:
        channels = []
    water_levels = { }
    for line in lines[1:]:
        if line=='\n':
            break
        v = line.split()
        if v[0] in channels:
            # Get flood level (Elevation)
            water_levels[v[0]] = v[5]
            # Get flood depth (Depth)
            # water_levels[int(v[0])] = v[2]
    return water_levels


def isfloat(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def extractForecastTimeseries(timeseries, extract_date, extract_time, by_day=False):
    """
    Extracted timeseries upward from given date and time
    E.g. Consider timeseries 2017-09-01 to 2017-09-03
    date: 2017-09-01 and time: 14:00:00 will extract a timeseries which contains
    values that timestamp onwards
    """
    print('LibForecastTimeseries:: extractForecastTimeseries')
    if by_day:
        extract_date_time = datetime.strptime(extract_date, '%Y-%m-%d')
    else:
        extract_date_time = datetime.strptime('%s %s' % (extract_date, extract_time), '%Y-%m-%d %H:%M:%S')

    is_date_time = isinstance(timeseries[0][0], datetime)
    new_timeseries = []
    for i, tt in enumerate(timeseries):
        tt_date_time = tt[0] if is_date_time else datetime.strptime(tt[0], '%Y-%m-%d %H:%M:%S')
        if tt_date_time >= extract_date_time:
            new_timeseries = timeseries[i:]
            break

    return new_timeseries


def save_forecast_timeseries_to_db(pool, timeseries, run_date, run_time, opts, flo2d_stations, fgt):
    print('EXTRACTFLO2DDISCHARGE:: save_forecast_timeseries >>', opts)

    # {
    #         'tms_id'     : '',
    #         'sim_tag'    : '',
    #         'station_id' : '',
    #         'source_id'  : '',
    #         'unit_id'    : '',
    #         'variable_id': ''
    #         }

    # Convert date time with offset
    date_time = datetime.strptime('%s %s' % (run_date, run_time), COMMON_DATE_TIME_FORMAT)
    if 'utcOffset' in opts:
        date_time = date_time + opts['utcOffset']
        run_date = date_time.strftime('%Y-%m-%d')
        run_time = date_time.strftime('%H:%M:%S')

    # If there is an offset, shift by offset before proceed
    forecast_timeseries = []
    if 'utcOffset' in opts:
        print('Shift by utcOffset:', opts['utcOffset'].resolution)
        for item in timeseries:
            forecast_timeseries.append(
                    [datetime.strptime(item[0], COMMON_DATE_TIME_FORMAT) + opts['utcOffset'], item[1]])

        forecast_timeseries = extractForecastTimeseries(timeseries=forecast_timeseries, extract_date=run_date,
                extract_time=run_time)
    else:
        forecast_timeseries = extractForecastTimeseries(timeseries=timeseries, extract_date=run_date,
                extract_time=run_time)

    elementNo = opts.get('elementNo')

    tms_meta = opts.get('tms_meta')

    tms_meta['latitude'] = str(flo2d_stations.get(elementNo)[1])
    tms_meta['longitude'] = str(flo2d_stations.get(elementNo)[2])
    tms_meta['station_id'] = flo2d_stations.get(elementNo)[0]

    try:

        TS = Timeseries(pool=pool)

        tms_id = TS.get_timeseries_id_if_exists(meta_data=tms_meta)

        if tms_id is None:
            tms_id = TS.generate_timeseries_id(meta_data=tms_meta)
            tms_meta['tms_id'] = tms_id
            TS.insert_run(run_meta=tms_meta)
            TS.update_start_date(id_=tms_id, start_date=fgt)

        TS.insert_data(timeseries=forecast_timeseries, tms_id=tms_id, fgt=fgt, upsert=True)
        TS.update_latest_fgt(id_=tms_id, fgt=fgt)

    except Exception:
        logger.error("Exception occurred while pushing data to the curw_fcst database")
        traceback.print_exc()


def usage():
    usageText = """
    ---------------------------------------------------------------------------
    Extract Flo2D 250, 150 & 150_v2 output discharge to the curw_fcst database.
    ----------------------------------------------------------------------------
    
    Usage: .\output\extract_discharge.py [-m flo2d_XXX] [-s "YYYY-MM-DD HH:MM:SS"] [-r "YYYY-MM-DD HH:MM:SS"] [-t XXX] 
    [-d "C:\\udp_150\\2019-09-23"] [-E]

    -h  --help          Show usage
    -m  --model         FLO2D model (e.g. flo2d_250, flo2d_150, flo2d_150_v2).
    -s  --ts_start_time Timeseries start time (e.g: "2019-06-05 23:00:00").
    -r  --run_time      Run time (e.g: "2019-06-05 23:00:00").
    -d  --dir           Output directory (e.g. "C:\\udp_150\\2019-09-23"); 
                        Directory where HYCHAN.OUT and TIMDEP.OUT files located.
    -t  --sim_tag       Simulation tag
    -E  --event_sim     Weather the output is from a event simulation or not (e.g. -E, --event_sim)
    """
    print(usageText)


if __name__ == "__main__":

    """
    dis_config.json 
    {
      "HYCHAN_OUT_FILE": "HYCHAN.OUT",
    
      "utc_offset": "",
    
      "sim_tag": "daily_run",
    
      "model": "FLO2D",
    
      "unit": "m3/s",
      "unit_type": "Instantaneous",
    
      "variable": "Discharge"
    }

    """

    set_db_config_file_path(os.path.join(ROOT_DIRECTORY, 'db_adapter_config.json'))

    try:

        in_ts_start_time = None
        in_run_time = None
        flo2d_model = None
        output_dir = None
        sim_tag = None
        event_sim = False

        try:
            opts, args = getopt.getopt(sys.argv[1:], "h:m:s:r:d:t:E",
                                       ["help", "model=", "ts_start_time=", "run_time=", "dir=", "sim_tag=", "event_sim"])
        except getopt.GetoptError:
            usage()
            sys.exit(2)
        for opt, arg in opts:
            if opt in ("-h", "--help"):
                usage()
                sys.exit()
            elif opt in ("-m", "--model"):
                flo2d_model = arg.strip()
            elif opt in ("-s", "--ts_start_time"):
                in_ts_start_time = arg.strip()
            elif opt in ("-r", "--run_time"):
                in_run_time = arg.strip()
            elif opt in ("-d", "--dir"):
                output_dir = arg.strip()
            elif opt in ("-t", "--sim_tag"):
                sim_tag = arg.strip()
            elif opt in ("-E", "--event_sim"):
                event_sim = True

        if event_sim:
            set_db_config_file_path(os.path.join(ROOT_DIRECTORY, 'db_adapter_config_event_sim.json'))

        config = json.loads(open(os.path.join(ROOT_DIRECTORY, 'output', 'dis_config.json')).read())

        # flo2D related details
        HYCHAN_OUT_FILE = read_attribute_from_config_file('HYCHAN_OUT_FILE', config, True)

        if in_ts_start_time is None:
            print("Please specify the time series start time.")
            usage()
            exit(1)
        if in_run_time is None:
            print("Please specify run time.")
            usage()
            exit(1)
        if flo2d_model is None:
            print("Please specify flo2d model.")
            usage()
            exit(1)
        if output_dir is None:
            print("Please specify flo2d output directory.")
            usage()
            exit(1)

        if not os.path.isdir(output_dir):
            print("Given output directory doesn't exist")
            exit(1)
        if flo2d_model not in ("flo2d_250", "flo2d_150", "flo2d_150_v2"):
            print("Flo2d model should be either \"flo2d_250\" or \"flo2d_150\" or \"flo2d_150_v2\"")
            exit(1)

        in_ts_start_time = check_time_format(in_ts_start_time)
        in_run_time = check_time_format(in_run_time)

        output_dir = output_dir

        # run_date = in_run_time.strftime("%Y-%m-%d")
        # run_time = in_run_time.strftime("%H:%M:%S")
        data_extraction_start = (in_ts_start_time + timedelta(minutes=15)).strftime(COMMON_DATE_TIME_FORMAT)
        run_date = data_extraction_start.split(' ')[0]
        run_time = data_extraction_start.split(' ')[1]

        ts_start_date = in_ts_start_time.strftime("%Y-%m-%d")
        ts_start_time = in_ts_start_time.strftime("%H:%M:%S")

        utc_offset = read_attribute_from_config_file('utc_offset', config, False)
        if utc_offset is None:
            utc_offset = ''

        # sim tag
        if sim_tag is None:
            sim_tag = read_attribute_from_config_file('sim_tag', config, True)

        # source details
        model = read_attribute_from_config_file('model', config, True)
        version = "_".join(flo2d_model.split("_")[1:])

        # unit details
        unit = read_attribute_from_config_file('unit', config, True)
        unit_type = UnitType.getType(read_attribute_from_config_file('unit_type', config, True))

        # variable details
        variable = read_attribute_from_config_file('variable', config, True)

        hychan_out_file_path = os.path.join(output_dir, HYCHAN_OUT_FILE)

        pool = get_Pool(host=con_params.CURW_FCST_HOST, port=con_params.CURW_FCST_PORT, db=con_params.CURW_FCST_DATABASE,
                        user=con_params.CURW_FCST_USERNAME, password=con_params.CURW_FCST_PASSWORD)

        flo2d_model_name = '{}_{}'.format(model, version)

        flo2d_source = json.loads(get_source_parameters(pool=pool, model=model, version=version))
        flo2d_stations = get_flo2d_output_stations(pool=pool, flo2d_model=StationEnum.getType(flo2d_model_name))

        source_id = get_source_id(pool=pool, model=model, version=version)

        variable_id = get_variable_id(pool=pool, variable=variable)

        unit_id = get_unit_id(pool=pool, unit=unit, unit_type=unit_type)

        tms_meta = {
                'sim_tag'    : sim_tag,
                'model'      : model,
                'version'    : version,
                'variable'   : variable,
                'unit'       : unit,
                'unit_type'  : unit_type.value,
                'source_id'  : source_id,
                'variable_id': variable_id,
                'unit_id'    : unit_id
                }

        CHANNEL_CELL_MAP = flo2d_source["CHANNEL_CELL_MAP"]

        FLOOD_PLAIN_CELL_MAP = flo2d_source["FLOOD_PLAIN_CELL_MAP"]

        ELEMENT_NUMBERS = CHANNEL_CELL_MAP.keys()
        FLOOD_ELEMENT_NUMBERS = FLOOD_PLAIN_CELL_MAP.keys()
        SERIES_LENGTH = 0
        MISSING_VALUE = -999

        utcOffset = getUTCOffset(utc_offset, default=True)

        print('Extract Discharge Result of FLO2D on', run_date, '@', run_time, 'with Base time of', ts_start_date,
                '@', ts_start_time)

        # Check HYCHAN.OUT file exists
        if not os.path.exists(hychan_out_file_path):
            print('Unable to find file : ', hychan_out_file_path)
            traceback.print_exc()
            exit(1)

        fgt = get_file_last_modified_time(hychan_out_file_path)

        #####################################
        # Calculate the size of time series #
        #####################################
        bufsize = 65536
        with open(hychan_out_file_path) as infile:
            isWaterLevelLines = False
            isCounting = False
            countSeriesSize = 0  # HACK: When it comes to the end of file, unable to detect end of time series
            while True:
                lines = infile.readlines(bufsize)
                if not lines or SERIES_LENGTH:
                    break
                for line in lines:
                    if line.startswith('CHANNEL HYDROGRAPH FOR ELEMENT NO:', 5):
                        isWaterLevelLines = True
                    elif isWaterLevelLines:
                        cols = line.split()
                        if len(cols) > 0 and cols[0].replace('.', '', 1).isdigit():
                            countSeriesSize += 1
                            isCounting = True
                        elif isWaterLevelLines and isCounting:
                            SERIES_LENGTH = countSeriesSize
                            break

        print('Series Length is :', SERIES_LENGTH)
        bufsize = 65536
        ####################################################
        # Extract Channel Discharge from HYCHAN.OUT file   #
        ####################################################
        print('Extract Channel Discharge Result of FLO2D (HYCHAN.OUT) on', run_date, '@', run_time,
                'with Base time of',
                ts_start_date, '@', ts_start_time)
        with open(hychan_out_file_path) as infile:
            isWaterLevelLines = False
            isSeriesComplete = False
            waterLevelLines = []
            seriesSize = 0  # HACK: When it comes to the end of file, unable to detect end of time series
            while True:
                lines = infile.readlines(bufsize)
                if not lines:
                    break
                for line in lines:
                    if line.startswith('CHANNEL HYDROGRAPH FOR ELEMENT NO:', 5):
                        seriesSize = 0
                        elementNo = line.split()[5]

                        if elementNo in ELEMENT_NUMBERS:
                            isWaterLevelLines = True
                            waterLevelLines.append(line)
                        else:
                            isWaterLevelLines = False

                    elif isWaterLevelLines:
                        cols = line.split()
                        if len(cols) > 0 and isfloat(cols[0]):
                            seriesSize += 1
                            waterLevelLines.append(line)

                            if seriesSize==SERIES_LENGTH:
                                isSeriesComplete = True

                    if isSeriesComplete:
                        baseTime = datetime.strptime('%s %s' % (ts_start_date, ts_start_time), '%Y-%m-%d %H:%M:%S')
                        timeseries = []
                        elementNo = waterLevelLines[0].split()[5]
                        # print('Extracted Cell No', elementNo, CHANNEL_CELL_MAP[elementNo])
                        for ts in waterLevelLines[1:]:
                            v = ts.split()
                            if len(v) < 1:
                                continue
                            # Get Discharge
                            value = v[4]
                            if not isfloat(value):
                                value = MISSING_VALUE
                                continue  # If value is not present, skip
                            if value=='NaN':
                                continue  # If value is NaN, skip
                            timeStep = float(v[0])
                            currentStepTime = baseTime + timedelta(hours=timeStep)
                            dateAndTime = currentStepTime.strftime("%Y-%m-%d %H:%M:%S")
                            timeseries.append([dateAndTime, value])

                        # Save Forecast values into Database
                        opts = {
                                'elementNo': elementNo,
                                'tms_meta' : tms_meta
                                }
                        # print('>>>>>', opts)
                        if utcOffset!=timedelta():
                            opts['utcOffset'] = utcOffset

                        # Push timeseries to database
                        save_forecast_timeseries_to_db(pool=pool, timeseries=timeseries,
                                run_date=run_date, run_time=run_time, opts=opts, flo2d_stations=flo2d_stations, fgt=fgt)

                        isWaterLevelLines = False
                        isSeriesComplete = False
                        waterLevelLines = []
                # -- END for loop
            # -- END while loop

        run_info = json.loads(open(os.path.join(os.path.dirname(hychan_out_file_path), "run_meta.json")).read())
        insert_run_metadata(pool=pool, source_id=source_id, variable_id=variable_id, sim_tag=sim_tag, fgt=fgt, metadata=run_info)
    except Exception as e:
        traceback.print_exc()
    finally:
        logger.info("Process finished.")
        print("Process finished.")