"""
lab  -  The optical reference library: 3D-printed ship photos -> measurements -> YOLO-OBB
training data -> reference signatures that satellite (optical and SAR) detections are
compared against.

  lab/ingest.py        find the photos and their sun / camera / rotation settings   (step 0)
  lab/annotate.py      segment hull + shadow, fix ring clipping, measure, label     (Module 1)
  lab/validate.py      check measurements against sun geometry                      (Module 2)
  lab/train.py         train / run YOLO-OBB and export the same measurements        (Module 3)
  lab/reference.py     build the SAR <-> optical reference table and look things up
  lab/crosscue.py      test the SAR <-> optical cross-cue (self-test + the daily pings)
  lab/run.py           all of the above in one go:  python -m lab.run "C:/path/to/photos"
  lab/settings.py      model scale, heights, camera convention, ring - EDIT THIS
  lab/synthetic.py     fake turntable photos for testing

Run from the repository folder, e.g.  python -m lab.ingest --images "D:/Ship Photos" --dry-run
Full guide: lab/README.md
"""
