CLEANUP: remove files that will not be used again
Tool-repo policy (ai-worldbuilder/rules/cleanup.md).
Tool: python3 scripts/site_cleanup.py report | due | apply | log      Log: site/CLEANUP-LOG.txt

PRINCIPLE
 A file stays only if something can still read it. Everything else is
 regenerable (delete), expired (delete) or judged junk (delete, keep a note).
 Reference imagery is kept at this time, except junk.

CLASSES
 SAFE (apply, no judgement needed)
  views          verification/site/views/*.png older than 7 days and named in no live site/*.txt
                 (site_views.py regenerates them).
  screenshots    instance/screenshots older than 7 days.
  cells-cache    backups/cells-cache pickles whose *-cells.json is gone (the rest is rebuilt on demand).
  audits         verification/site/audits and patrol: newest 10 files each.
 JUDGED (apply --class reference-junk)
  reference-junk reference files with useful=no, or with no usable data (blank, one colour, fully
                 transparent aerial tile). The file goes; its manifest row stays with useful=no,
                 removed=<date>, removed_reason, url and sha256, so grep-before-download still
                 stops a refetch. Reasons are also written to CLEANUP-LOG.txt.
 REPORT ONLY (a person or agent decides; the tool never deletes)
  reference-unlogged  images under reference/aerial|photos that the manifest does not list. Log each
                 with ref_manifest.py (set useful once) or delete it and say why in CLEANUP-LOG.txt.
  build-scripts  scripts/build_*.py untouched 14+ days and referenced by no other script or test.
                 A built cell is done, but a script can be re-run for a refinement. Retire by
                 tar to backups/retired-<what>-<date>.tar.gz, git rm, one line in NOTES.txt (as
                 with the ground scripts).

NEVER TOUCHED
 instance/saves (the live world), backups/pristine-geocraft (legacy baseline only;
 not the live lidar evidence path), backups/*.tar.gz, verification/site/*-cells.json (build records:
 owners db, roof_lint, audit drift) and *-backup.json (undo), reference useful yes/maybe,
 site/archive, downloads/ (source data).

WHEN
 Due when the last logged apply is 7+ days old, or the SAFE classes would free 1 GB+
 (`site_cleanup.py due` exits 0 with one line, else 1).
 - Session end: after site_archive, run `due`. If due, `apply`, then `report` and settle the
   REPORT-ONLY and JUDGED lists as one small queue item.
 - Never while another builder holds site/LOCK.json and is mid-apply.
 - Tuning: constants at the top of scripts/site_cleanup.py.
