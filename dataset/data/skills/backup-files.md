---
name: backup-files
description: Make a dated archive of chosen files, verify it, and tell the user exactly
  where it is.
when_to_use: backup, archive, zip, compress, save a copy, copy my files, snapshot
tools: list_dir, find_files, run_command, copy_file, write_file, notify, ask
version: '1.0'
created: '2026-09-26T21:39:29'
updated: '2026-09-26T21:39:29'
---

# Backing up files

## Steps
1. **Agree on scope and destination.** Which folder(s), and where the backup
   should land (another drive, a network share, a dated folder). A backup on the
   same disk as the original does not survive the failure it is meant for; say so
   if that is the only option.
2. **Make a dated, self-describing archive:** `run_command` something like
   `Compress-Archive -Path <folder> -DestinationPath <dest>/backup-YYYY-MM-DD.zip`
   (or `tar -czf`). The date in the name lets a folder of backups sort itself.
3. **Verify the archive** - do not trust the exit code alone. Check the file
   exists, its size is plausible for the source, and ideally that you can list
   its contents back.
4. **Report** the full path, the size, and what was included or skipped.

## Rules
- Never overwrite an existing backup. A second backup to the same name destroys
  the first one silently.
- If the source contains credentials or keys, say so before copying them to a
  destination the user did not specifically name.
