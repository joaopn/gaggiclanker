# Simulator patches

`scripts/sim.sh` applies every `*.patch` here to its scratch clone of the
GaggiMate firmware before building the simulator. The directory is empty since
firmware v1.9.0, which carries the `AsyncURIMatcher` the simulator used to be
missing. Add a patch (cut with `git diff` against the pinned firmware release)
only when the simulator stops building for a reason that lives in the firmware.
