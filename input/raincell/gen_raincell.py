#!"D:\curw_flo2d_data_manager\venv\Scripts\python.exe"
import pymysql
import getopt
from datetime import datetime, timedelta
import traceback
import os
import sys
import json

DATE_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'
from db_adapter.constants import set_db_config_file_path
from db_adapter.constants import connection as con_params
# from db_adapter.constants import CURW_SIM_DATABASE, CURW_SIM_PASSWORD, CURW_SIM_USERNAME, CURW_SIM_PORT, CURW_SIM_HOST
ROOT_DIRECTORY = 'D:\curw_flo2d_data_manager'


def save_metadata_to_file(input_filepath, metadata):

    metadata_filepath = os.path.join(os.path.dirname(input_filepath), "run_meta.json")

    updated_metadata = {}
    try:
        existing_metadata = json.loads(open(metadata_filepath).read())
        updated_metadata = existing_metadata
    except FileNotFoundError as eFNFE:
        pass

    for key in metadata.keys():
        updated_metadata[key] = metadata[key]

    with open(metadata_filepath, 'w') as outfile:
        json.dump(updated_metadata, outfile)


def write_to_file(file_name, data):
    with open(file_name, 'w+') as f:
        f.write('\n'.join(data))


def append_to_file(file_name, data):
    with open(file_name, 'a+') as f:
        f.write('\n'.join(data))


def makedir_if_not_exist_given_filepath(filename):
    if not os.path.exists(os.path.dirname(filename)):
        try:
            os.makedirs(os.path.dirname(filename))
        except OSError as exc:  # Guard against race condition
            pass


def check_time_format(time, model):
    try:
        time = datetime.strptime(time, DATE_TIME_FORMAT)

        if time.strftime('%S') != '00':
            print("Seconds should be always 00")
            exit(1)
        if model=="flo2d_250" and time.strftime('%M') not in ('05', '10', '15', '20', '25', '30', '35', '40', '45', '50', '55', '00'):
            print("Minutes should be multiple of 5 fro flo2d_250")
            exit(1)
        if model in ("flo2d_150", "flo2d_150_v2") and time.strftime('%M') not in ('15', '30', '45', '00'):
            print("Minutes should be multiple of 15 for flo2d_150")
            exit(1)

        return True
    except Exception:
        traceback.print_exc()
        print("Time {} is not in proper format".format(time))
        exit(1)


def prepare_raincell(raincell_file_path, start_time, end_time,
                                target_model="flo2d_250", interpolation_method="MME"):

    """
    Create raincell for flo2d
    :param raincell_file_path:
    :param start_time: Raincell start time (e.g: "2019-06-05 00:00:00")
    :param end_time: Raincell start time (e.g: "2019-06-05 23:30:00")
    :param target_model: FLO2D model (e.g. flo2d_250, flo2d_150)
    :param interpolation_method: value interpolation method (e.g. "MME")
    :return:
    """
    connection = pymysql.connect(host=con_params.CURW_SIM_HOST, user=con_params.CURW_SIM_USERNAME,
                                 password=con_params.CURW_SIM_PASSWORD, db=con_params.CURW_SIM_DATABASE,
            cursorclass=pymysql.cursors.DictCursor)
    print("Connected to database")

    end_time = datetime.strptime(end_time, DATE_TIME_FORMAT)
    start_time = datetime.strptime(start_time, DATE_TIME_FORMAT)

    if end_time < start_time:
        print("start_time should be less than end_time")
        exit(1)

    # find max end time
    try:
        with connection.cursor() as cursor0:
            cursor0.callproc('get_ts_end', (target_model, interpolation_method))
            max_end_time = cursor0.fetchone()['time']

    except Exception as e:
        traceback.print_exc()
        max_end_time = datetime.strptime((datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d 23:30:00'),
                DATE_TIME_FORMAT)

    min_start_time = datetime.strptime("2019-06-28 00:00:00", DATE_TIME_FORMAT)

    if end_time > max_end_time:
        end_time = max_end_time

    if start_time < min_start_time:
        start_time = min_start_time

    if target_model=="flo2d_250":
        timestep = 5
        water_supply = 1 / (24 * 12)
    elif target_model in ("flo2d_150", "flo2d_150_v2"):
        timestep = 15
        water_supply = 1 / (24 * 4)

    length = int(((end_time-start_time).total_seconds()/60)/timestep)

    write_to_file(raincell_file_path,
            ['{} {} {} {}\n'.format(timestep, length, start_time.strftime(DATE_TIME_FORMAT), end_time.strftime(DATE_TIME_FORMAT))])
    try:
        timestamp = start_time
        while timestamp < end_time:
            raincell = []
            timestamp = timestamp + timedelta(minutes=timestep)
            count=1
            # Extract raincell from db
            with connection.cursor() as cursor1:
                cursor1.callproc('prepare_flo2d_raincell', (target_model, interpolation_method, timestamp))
                for result in cursor1:
                    corrected_rain_value = float(result.get('value')) + water_supply
                    raincell.append('{} {}'.format(result.get('cell_id'), '%.3f' % corrected_rain_value))
                raincell.append('')
            append_to_file(raincell_file_path, raincell)
            print(timestamp)
    except Exception as ex:
        traceback.print_exc()
    finally:
        connection.close()
        print("{} raincell generation process completed".format(datetime.now()))


def create_dir_if_not_exists(path):
    """
    create directory(if needed recursively) or paths
    :param path: string : directory path
    :return: string
    """
    if not os.path.exists(path):
        os.makedirs(path)

    return path


def usage():
    usageText = """
    ---------------------------------------------
    Prepare raincell for Flo2D 250, 150 & 150_v2
    ---------------------------------------------
    
    Usage: .\input\\raincell\gen_raincell.py [-m flo2d_XXX][-s "YYYY-MM-DD HH:MM:SS"] [-e "YYYY-MM-DD HH:MM:SS"] [-d "directory_path"] [-M XXX] [-E]
    
    -h  --help          Show usage
    -m  --model         FLO2D model (e.g. flo2d_250, flo2d_150). Default is flo2d_250.
    -s  --start_time    Raincell start time (e.g: "2019-06-05 00:00:00"). Default is 23:30:00, 3 days before today.
    -e  --end_time      Raincell end time (e.g: "2019-06-05 23:30:00"). Default is 23:30:00, tomorrow.
    -d  --dir           Raincell file generation location (e.g: "C:\\udp_150\\2019-09-23")
    -M  --method        Raincell calculation method (e.g: "MME", "OBS")
    -E  --event_sim     Weather the raincell is prepared for event simulation or not (e.g. -E, --event_sim)
    """
    print(usageText)


if __name__=="__main__":

    set_db_config_file_path(os.path.join(ROOT_DIRECTORY, 'db_adapter_config.json'))

    try:
        start_time = None
        end_time = None
        flo2d_model = None
        method = "MME"
        output_dir = None
        file_name = 'RAINCELL.DAT'
        event_sim = False

        try:
            opts, args = getopt.getopt(sys.argv[1:], "h:m:s:e:d:M:E",
                    ["help", "flo2d_model=", "start_time=", "end_time=", "dir=", "method=", "event_sim"])
        except getopt.GetoptError:
            usage()
            sys.exit(2)
        for opt, arg in opts:
            if opt in ("-h", "--help"):
                usage()
                sys.exit()
            elif opt in ("-m", "--flo2d_model"):
                flo2d_model = arg.strip()
            elif opt in ("-s", "--start_time"):
                start_time = arg.strip()
            elif opt in ("-e", "--end_time"):
                end_time = arg.strip()
            elif opt in ("-d", "--dir"):
                output_dir = arg.strip()
            elif opt in ("-M", "--method"):
                method = arg.strip()
            elif opt in ("-E", "--event_sim"):
                event_sim = True

        if event_sim:
            set_db_config_file_path(os.path.join(ROOT_DIRECTORY, 'db_adapter_config_event_sim.json'))

        if flo2d_model is None:
            flo2d_model = "flo2d_250"
        elif flo2d_model not in ("flo2d_250", "flo2d_150", "flo2d_150_v2"):
            print("Flo2d model should be either \"flo2d_250\" or \"flo2d_150\" or \"flo2d_150_v2\"")
            exit(1)

        if start_time is None:
            start_time = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d 23:30:00')
        else:
            check_time_format(time=start_time, model=flo2d_model)

        if end_time is None:
            end_time = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d 23:30:00')
        else:
            check_time_format(time=end_time, model=flo2d_model)


        if output_dir is not None:
            raincell_file_path = os.path.join(output_dir, file_name)
        else:
            raincell_file_path = os.path.join(r"D:\raincell",
                                              'RAINCELL_{}_{}_{}.DAT'.format(flo2d_model, start_time, end_time).replace(
                                                  ' ', '_').replace(':', '-'))

        makedir_if_not_exist_given_filepath(raincell_file_path)

        if not os.path.isfile(raincell_file_path):
            print("{} start preparing raincell".format(datetime.now()))
            prepare_raincell(raincell_file_path,
                    target_model=flo2d_model, start_time=start_time, end_time=end_time, interpolation_method=method)
            # print(raincell_file_path, flo2d_model, start_time, end_time)
            metadata = {
                "raincell": {
                    "tag": method,
                    "model": flo2d_model
                }
            }
            save_metadata_to_file(input_filepath=raincell_file_path, metadata=metadata)
            print("{} completed preparing raincell".format(datetime.now()))
        else:
            print('Raincell file already in path : ', raincell_file_path)


    except Exception:
        traceback.print_exc()
