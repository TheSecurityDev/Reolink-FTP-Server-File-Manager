# Reolink FTP Server Upload File Manager, made by TheSecurityDev.
# 
# It's made to run on a FTP server (like a Raspberry Pi), for use with Reolink NVRs and security cameras to automatically delete old recordings, and organize the files, etc.
# The script runs once, so you have to use a cronjob to execute periodically.
# 
# You can setup the script to run automatically by typing 'crontab -e' in your linux terminal, and adding a line like this:
#   */5 * * * * python3 /home/pi/manage_uploaded_files_task.py >> /home/pi/manage_uploaded_files_task.log 2>&1  # Runs the script every 5 minutes
#   # You'll probably need to change your path and file names!
# 
# REQUIREMENTS:
#   Requires Python 3.9. Requires the humanize library, which you should be able to install with 'pip3 install humanize'.
#     You can use Python 3.7 (default on Raspberry Pi), but you'll have some errors to fix, mainly related to static typing.
#     So for example, you'll have to change. 'list[RecordedFile]' to just 'list' for statically typed return types.


import os
import re
import shutil
from datetime import datetime

from humanize import naturalsize
from humanize.time import precisedelta

HOME_PATH = os.path.expanduser('~')

# You can set these values however you want
upload_dir = os.path.join(HOME_PATH, "")  # Where the files are uploaded by the device. NOTE: In my NVR settings, for FTP upload options there is a place to input a directory to upload. However, it doesn't seem to actually work (always uploads to HOME_PATH), so that's why I made my own archiving script as well.
archive_dir = upload_dir + os.sep + "Archive"  # Where the files are stored (moved from the upload_dir)
min_unmodified_mins_before_archive = 5  # Only archive files that haven't been modified in this much time (prevent archiving files that are still being uploaded). Honestly it's probably ok to do a minute or less, but I just wanted to be safe.
min_free_space_mb = 2000  # The amount of storage space (MB) at which a deletion will be triggered. If the free space goes below this value, then it will try to delete old files until the remaining storage space goes above this value plus the specified extra amount.
extra_mb_to_delete = 500  # After the delete threshold is reached, free this amount (MB) past the threshold. You can use this to free up extra space for the files before they are archived, since the deletion is run first.
# Debug settings (change to False in production)
verbose_logging = True
simulate_delete_files = True
simulate_move_files = True
simulate_delete_empty_subdirs = True

# You shouldn't change these values
# They are used to detect the recording files
PHOTO_FILE_EXTENSION = '.jpg'
VIDEO_FILE_EXTENSION = '.mp4'
device_name_regex_string = r'[a-zA-Z\d \-=+\[\]{}]+'  # These seem to be the only characters that Reolink allows for device names
date_regex_string = r'([1-3]\d\d\d)([0-1]\d)([0-3]\d)([0-2]\d)([0-5]\d)([0-5]\d)'
base_file_name_regex_string = '({})_([\d]*)_?{}'.format(device_name_regex_string, date_regex_string)
photo_file_name_regex = re.compile('^{}{}$'.format(base_file_name_regex_string, PHOTO_FILE_EXTENSION))
video_file_name_regex = re.compile('^{}{}$'.format(base_file_name_regex_string, VIDEO_FILE_EXTENSION))


# Terminal colors/formatting
class bcolors:              # Typical use
    RED = '\033[91m'        # Error
    GREEN = '\033[92m'      # Success
    YELLOW = '\033[93m'     # Warning
    BLUE = '\033[94m'       # Performing action / Info
    PURPLE = '\033[95m'     # Start/End (headers)
    CYAN = '\033[96m'       # Info
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    ENDC = '\033[0m'


class RecordedFile(object):
    TYPE_PHOTO = "PHOTO"
    TYPE_VIDEO = "VIDEO"

    type = None
    name = ""
    path = ""
    size = 0
    last_modified_ts = 0
    device_name = ""
    channel = None
    datetime = None

    def __init__(self, type: int, file_name: str, file_path: str, regex_match):
        # Set recording type
        if type == self.TYPE_PHOTO or type == self.TYPE_VIDEO:
            self.type = type
        else:
            self.error("Unknown file type '{}'.".format(type))
       
        # Set file name and path
        self.name = file_name
        self.path = file_path
        
        if not os.path.isfile(file_path):
            self.error("'{}' is not a valid file.".format(file_path))
        else:
            self.size = os.path.getsize(file_path)  # Set file size
            self.last_modified_ts = os.path.getmtime(file_path)  # Set the file last modified value

        if regex_match:
            # Get details from file name
            groups = regex_match.groups()
            self.device_name = groups[0]
            self.channel_str = groups[1]
            self.channel_num = None
            if self.channel_str:
                self.channel_num = int(self.channel_str) + 1  # Add 1 to the channel num so it matches what's shown in the Reolink client (which starts from 00)
            # DateTime
            year = groups[2]
            month = groups[3]
            day = groups[4]
            hour = groups[5]
            minute = groups[6]
            second = groups[7]
            self.datetime = datetime.fromisoformat('{}-{}-{}T{}:{}:{}'.format(year, month, day, hour, minute, second))
        
        # self.print_debug()
    
    def generate_archive_dir_string(self) -> str:
        date_path_str = self.datetime.strftime("{0}%Y{0}%m{0}%d{0}".format(os.sep))  # Format like '/2021/01/01', using your OS path separator for the slash
        return archive_dir + date_path_str

    def error(self, msg: str):
        print_red("Error while creating the RecordedFile object!\n\tReason: {}".format(msg))
    
    def print_debug(self):
        print("Type: {}; Name: '{}'; Path: '{}'; Size: {}; DeviceName: '{}'; Channel: {} ('{}'); DateTime: {}".format(self.type, self.name, self.path, self.size, self.device_name, self.channel_num, self.channel_str, self.datetime))



def main():
    delete_old_files_if_necessary()
    archive_new_files()
    delete_empty_sub_dirs(archive_dir)



def delete_old_files_if_necessary():
    bytes_to_free = check_bytes_to_free()
    if bytes_to_free > 0:
        # Delete files
        (files_to_delete, files_to_delete_total_size) = get_all_old_files_to_delete(bytes_to_free)
        delete_files(files_to_delete, files_to_delete_total_size)


def check_bytes_to_free() -> int:
    # Check how much (if any) space we need to free
    min_free_bytes = mb_to_bytes(min_free_space_mb)
    free_bytes = get_free_bytes(archive_dir)
    if free_bytes <= min_free_bytes:
        bytes_to_free = (min_free_bytes - free_bytes) + mb_to_bytes(extra_mb_to_delete)
        print_yellow("We need to free some space! ({} above the threshold). Will try to free at least {}.".format(humanizesize(min_free_bytes - free_bytes), humanizesize(bytes_to_free)))
        return bytes_to_free
    else:
        print_green("We don't need to free any space. ({} below the threshold)".format(humanizesize(free_bytes - min_free_bytes)))
        return 0


def get_all_old_files_to_delete(min_total_bytes: int) -> tuple[list[RecordedFile], int]:
    print_blue("Checking for old files to delete...")
    # Go through each folder in the archives, starting from the oldest files, until we have the correct amount to delete or there are no more files
    all_files_to_delete: list[RecordedFile] = []
    total_size: int = 0
    # Year folders
    year_dirs = get_sub_dirs(archive_dir, full_paths=True)
    for year_dir in year_dirs:
        if total_size >= min_total_bytes:
            break
        # Month folders
        month_dirs = get_sub_dirs(year_dir, full_paths=True)
        for month_dir in month_dirs:
            if total_size >= min_total_bytes:
                break
            # Day folders
            day_dirs = get_sub_dirs(month_dir, full_paths=True)
            for day_dir in day_dirs:
                if total_size >= min_total_bytes:
                    break
                # Get files to delete from current directory, and add to the total list and total size value
                (this_dir_files, this_dir_total_size) = get_oldest_files_from_directory(day_dir, min_total_bytes - total_size)
                all_files_to_delete.extend(this_dir_files)
                total_size += this_dir_total_size
        
    return (all_files_to_delete, total_size)


def get_oldest_files_from_directory(directory: str, min_total_bytes: int) -> tuple[list[RecordedFile], int]:
    # Load archived files in a single directory and get the oldest first up to the specified amount of bytes
    oldest_files: list[RecordedFile] = []
    total_size: int = 0
    recorded_files: list[RecordedFile] = get_recorded_files(directory, oldest_first=True)
    for recorded_file in recorded_files:
        if total_size >= min_total_bytes:
            break
        else:
            oldest_files.append(recorded_file)
            total_size += recorded_file.size
    
    return (oldest_files, total_size)


def delete_files(files_to_delete: list[RecordedFile], files_to_delete_total_size: int):
    num_of_files_to_delete = len(files_to_delete)
    # Display info about files to delete
    if num_of_files_to_delete == 0:
        print_cyan("No files to delete.")
    else:
        print_blue("Deleting {} file{} ({})...".format(num_of_files_to_delete, "" if num_of_files_to_delete == 1 else "s", humanizesize(files_to_delete_total_size)))

        # Delete files
        deleted_files = 0
        deleted_total_size = 0
        failed_files = 0
        for recorded_file in files_to_delete:
            path = recorded_file.path
            try:
                if verbose_logging:
                    print("\tDeleting '{}'...".format(path))
                if simulate_delete_files:
                    print_yellow("\tSimulated delete file: '{}'".format(path))
                else:
                    os.remove(path)
                deleted_files += 1
                deleted_total_size += recorded_file.size
            except OSError as e:
                print_red("\tError deleting '{}'!\n\tReason: {}".format(path, e))
                failed_files += 1
        
        # Print results
        if deleted_files > 0:
            print_green("Successfully deleted {} file{} ({}).".format(deleted_files, "" if deleted_files == 1 else "s", humanizesize(deleted_total_size)))
        if failed_files > 0:
            print_yellow("Failed to delete {} file{}.".format(failed_files, "" if failed_files == 1 else "s"))



def archive_new_files():
    print_blue("Checking for files to archive...")
    new_files_to_archive: list[RecordedFile] = get_recorded_files(upload_dir, min_mod_age=60*min_unmodified_mins_before_archive, oldest_first=True)  # Get all the newly uploaded files that are older than the specified minutes
    num_of_files_to_archive = len(new_files_to_archive)
    if num_of_files_to_archive == 0:
        print_cyan("No files to archive.")
    else:
        print_blue("Archiving {} file{}...".format(num_of_files_to_archive, "" if num_of_files_to_archive == 1 else "s"))
        
        # Archive files (move to the archive directory)
        moved_files = 0
        moved_files_total_size = 0
        failed_files = 0
        for recorded_file in new_files_to_archive:
            current_path = recorded_file.path
            new_path = recorded_file.generate_archive_dir_string()
            try:
                if verbose_logging:
                    print("\tMoving '{}' to '{}'...".format(current_path, new_path))
                if simulate_move_files:
                    print_yellow("\tSimulated move file: '{}' to '{}'...".format(current_path, new_path))
                else:
                    if not os.path.exists(new_path):
                        os.makedirs(new_path)
                    shutil.move(current_path, new_path)
                moved_files += 1
                moved_files_total_size += recorded_file.size
            except OSError as e:
                print_red("\tError moving '{}' to '{}'!\n\tReason: {}".format(current_path, new_path, e))
                failed_files += 1
        
        # Print results
        if moved_files > 0:
            print_green("Successfully moved {} file{} ({}).".format(moved_files, "" if moved_files == 1 else "s", humanizesize(moved_files_total_size)))
        if failed_files > 0:
            print_yellow("Failed to move {} file{}.".format(failed_files, "" if failed_files == 1 else "s"))



def get_recorded_files(directory: str, min_mod_age: int = 0, include_video: bool = True, include_photo: bool = True, oldest_first: bool = True) -> list[RecordedFile]:
    # Create the directory if it doesn't exist
    if not os.path.exists(directory):
        os.makedirs(directory)
    # Get all the recorded files
    recorded_files: list[RecordedFile] = []
    dir_list = os.listdir(directory)  # Get the files and folders in the directory
    for file_name in dir_list:
        file_path = os.path.join(directory, file_name)
        if os.path.isfile(file_path):  # Only list files
            if include_photo:
                # Check if file name matches photo name regex
                photo_match = photo_file_name_regex.match(file_name)
                if photo_match:
                    # Create RecordedFile object and add to list if it matches the last modification time requirement
                    recorded_photo = RecordedFile(RecordedFile.TYPE_PHOTO, file_name, file_path, photo_match)
                    if has_recorded_file_been_unmodified_for_(recorded_photo, min_mod_age):
                        recorded_files.append(recorded_photo)
                    continue  # Since it's a photo, skip checking if it's a video
            if include_video:
                # Check if file name matches video name regex
                video_match = video_file_name_regex.match(file_name)
                if video_match:
                    # Create RecordedFile object and add to list if it matches the last modification time requirement
                    recorded_video = RecordedFile(RecordedFile.TYPE_VIDEO, file_name, file_path, video_match)
                    if has_recorded_file_been_unmodified_for_(recorded_video, min_mod_age):
                        recorded_files.append(recorded_video)
                    continue  # Not really necessary, since it's the last one
    
    recorded_files.sort(key=lambda recorded_file: recorded_file.datetime.timestamp(), reverse=not oldest_first)  # Sort by time
    return recorded_files


def has_recorded_file_been_unmodified_for_(recorded_file: RecordedFile, seconds: int) -> bool:
    return datetime.now().timestamp() > (recorded_file.last_modified_ts + seconds)



def get_sub_dirs(directory: str, full_paths: bool = False) -> list[str]:
    sub_dirs = []
    if os.path.exists(directory):
        for sub_dir_name in os.listdir(directory):
            sub_dir_path = os.path.join(directory, sub_dir_name)
            if os.path.isdir(sub_dir_path):
                if full_paths:
                    sub_dirs.append(sub_dir_path)
                else:
                    sub_dirs.append(sub_dir_name)
    return sub_dirs


def delete_empty_sub_dirs(directory: str):
    print_blue("Checking for and removing empty subfolders in '{}'...".format(directory))
    dirs_deleted = 0
    walk = list(os.walk(directory))
    for path, _, _ in walk[::-1]:
        if len(os.listdir(path)) == 0:  # If the directory is empty
            # Don't delete if it's the root directory
            if path == directory:
                if verbose_logging:
                    print("\tSkipping root folder, even though it was empty.")
                    continue
            # Delete the directory
            try:
                if verbose_logging:
                    print("\tDeleting empty directory '{}'...".format(path))
                if simulate_delete_empty_subdirs:
                    print_yellow("\tSimulated delete directory: '{}'".format(path))
                else:
                    os.rmdir(path)
                dirs_deleted += 1
            except OSError as e:
                print_red("\tError removing empty directory '{}'!\n\tReason: {}".format(path, e))
    if dirs_deleted > 0:
        print_green("Deleted {} empty folders.".format(dirs_deleted))
    else:
        print_cyan("No empty folders.")


def get_free_bytes(directory: str) -> int:
    if not os.path.exists(directory):
        os.makedirs(directory)
    return shutil.disk_usage(directory).free


# def bytes_to_mb(bytes: int, round_to: int) -> float:
#     return round(bytes / 1000 / 1000, round_to)  # Using MB (1000), instead of MiB (1024)

def mb_to_bytes(mb: float) -> int:
    return mb * 1000 * 1000  # Using MB (1000), instead of MiB (1024)

# naturalsize, but with default 2 decimals
def humanizesize(bytes: int, decimals: int = 2) -> str:
    return naturalsize(bytes, format="%.{}f".format(decimals))


# Terminal print functions
def print_red(msg: str):
    print(bcolors.RED + msg + bcolors.ENDC)

def print_yellow(msg: str):
    print(bcolors.YELLOW + msg + bcolors.ENDC)

def print_green(msg: str):
    print(bcolors.GREEN + msg + bcolors.ENDC)

def print_blue(msg: str):
    print(bcolors.BLUE + msg + bcolors.ENDC)

def print_cyan(msg: str):
    print(bcolors.CYAN + msg + bcolors.ENDC)

def print_purple(msg: str):
    print(bcolors.PURPLE + msg + bcolors.ENDC)



if __name__ == "__main__":
    start_time = datetime.now()
    print_purple("[{}] - Script started".format(start_time))
    try:
        main()
        end_time = datetime.now()
        print_purple("[{}] - Script finished (took {})\n".format(end_time, precisedelta(end_time - start_time, minimum_unit="milliseconds")))
    except Exception as e:
        print(bcolors.RED + "[{}] - Script terminated with an error: '{}'".format(datetime.now(), e))
        raise e

