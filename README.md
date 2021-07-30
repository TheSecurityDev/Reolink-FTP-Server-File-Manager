### Reolink FTP Server Upload File Manager, made by TheSecurityDev.

It's made to run on an FTP server (like a Raspberry Pi), for use with Reolink NVRs and security cameras to automatically delete old recordings, and organize the files, etc.
The script runs once, so you have to use a cronjob to execute periodically.

You can setup the script to run automatically by typing `crontab -e` in your linux terminal, and adding a line like this:
  
  `*/5 * * * * python3 /home/pi/manage_uploaded_files_task.py >> /home/pi/manage_uploaded_files_task.log 2>&1  # Runs the script every 5 minutes, and logs to file`
  #### You'll probably need to change your path and file names!

### REQUIREMENTS:
  Requires Python 3.9. Requires the humanize library, which you should be able to install with 'pip3 install humanize'.
    You _can_ use Python 3.7 (default on Raspberry Pi), but you'll have some errors to fix, mainly related to static typing.
    So for example, you'll have to change. `list[RecordedFile]` to just `list` for statically typed return types.

### You also need to modify the specified variables to set it up correctly.
