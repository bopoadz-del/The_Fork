Save to Downloads (not Drive):

  cd C:\Users\shimm\The_Fork
  git fetch origin cursor/dg2-pack-synthetic-docs-5b2e
  git checkout cursor/dg2-pack-synthetic-docs-5b2e
  git pull
  uploads\_synthetic\screenshots\copy-to-downloads.bat

Files land at:
  C:\Users\shimm\Downloads\the_fork_screenshots

Open these after:  git pull origin cursor/dg2-pack-synthetic-docs-5b2e

Windows path:
  C:\Users\shimm\The_Fork\uploads\_synthetic\screenshots

infra_pack_ui_aug23\     today's new-pack UI (project f1c78383)
  01_files_list.png
  02_m1_wbs.png
  03_m2_cashflow.png
  04_m3_commissioning_header.png
  05_m3_pour_record.png

leftover_holiday_spark\  leftover khor waterproofing table (ASTM D4787)
  01_leftover_holiday_spark_full.png
  02_leftover_holiday_spark_table.png

all_png\                 every other named UI still

These live under /uploads which .dockerignore already excludes, so a
Render deploy of main does not copy this folder into the image.
Contract Drive dumps stay gitignored and are not in this folder.
