# Digit v5-p2 Blender export and kinematic check

Use this workflow to export the authoritative Digit v5-p2 Blender model to ALF
and compare it with a local `ar-control` build.

## Prerequisites

Prepare these checkouts and tools:

- Blender 5.1.x.
- The `blender-data` repository at the revision to export.
- The `agility-software` repository containing the `ar-control` changes to test.
- A second `agility-software` checkout on Andy's `feature/hardware-analysis`
  branch. This branch contains the `mechanism` Blender extension.
- The ALF repository that receives the exported USD files and runs the check.
- The normal Bazel and Isaac Sim credentials and dependencies for those
  repositories.

Set checkout locations through environment variables:

```bash
export ALF_REPO=/path/to/alf
export AGILITY_SOFTWARE_REPO=/path/to/agility-software
export HARDWARE_ANALYSIS_REPO=/path/to/agility-software-hardware-analysis
export BLENDER_DATA_REPO=/path/to/blender-data
```

Confirm that the extension checkout is on the intended branch:

```bash
git -C "$HARDWARE_ANALYSIS_REPO" switch feature/hardware-analysis
```

## Export the USD files

The stock articulated gripper comes from `digit-v5-p2-gripper.blend`:

```bash
export BLEND_FILE="$BLENDER_DATA_REPO/assets/robots/digit/v5-p2/digit-v5-p2-gripper.blend"
export USD_FILE="$ALF_REPO/src/agility/core/models/usd/digit-v5-p2/digit-v5-p2.usda"
export MECHANISM_EXTENSIONS_DIR="$HARDWARE_ANALYSIS_REPO/package/blender-extensions"
export ACTUATORS_BLEND="$BLENDER_DATA_REPO/assets/parts/actuators.blend"
export EXPORT_STATUS_FILE="$(mktemp)"
export COLLISION_SET=detailed
```

Use `detailed` to preserve the collision fidelity of the established ALF model.
This selects `coarse-collisions` for the legs and `detail-collisions` for the
arms and torso. Use `minimal` only when the smaller collision representation is
intended.

The mechanism extension **must be imported and registered before Blender opens
the blend file**. Do not pass the blend file to the `blender` command because
Blender would open it before running the Python script. Run the export in
foreground mode; do not add `--background`.

Save the following script as `export_v5_p2_usd.py` outside the repositories:

```python
import os
import re
import sys
import traceback
from pathlib import Path

import bpy


extensions_dir = Path(os.environ["MECHANISM_EXTENSIONS_DIR"])
source = Path(os.environ["BLEND_FILE"])
output = Path(os.environ["USD_FILE"])
actuators = Path(os.environ["ACTUATORS_BLEND"])
status = Path(os.environ["EXPORT_STATUS_FILE"])
collision_set = os.environ["COLLISION_SET"]

usd_wheels = list(
    (extensions_dir / "mechanism" / "wheels").glob("agility_mujoco_usd-*.whl")
)
if len(usd_wheels) != 1:
    raise RuntimeError(f"Expected one agility_mujoco_usd wheel, found {usd_wheels}")

sys.path[:0] = [str(usd_wheels[0]), str(extensions_dir)]

import mechanism


def select_collisions() -> None:
    if collision_set not in {"detailed", "minimal"}:
        raise RuntimeError(f"Unknown collision set: {collision_set}")

    expected_counts = {
        "minimal-collisions": 5,
        "coarse-collisions": 2,
        "detail-collisions": 3,
    }
    actual_counts = {name: 0 for name in expected_counts}
    for collection in bpy.data.collections:
        if collection.library is not None:
            continue
        canonical_name = re.sub(r"\.\d{3}$", "", collection.name)
        if canonical_name not in expected_counts:
            continue
        if canonical_name == "minimal-collisions":
            collection.ar.exclude = collision_set == "detailed"
        else:
            collection.ar.exclude = collision_set == "minimal"
        actual_counts[canonical_name] += 1

    if actual_counts != expected_counts:
        raise RuntimeError(
            f"Unexpected collision collection counts: {actual_counts}; "
            f"expected {expected_counts}"
        )


def export() -> None:
    bpy.ops.wm.open_mainfile(filepath=str(source))

    for library in bpy.data.libraries:
        if Path(bpy.path.abspath(library.filepath)).exists():
            continue
        if Path(library.filepath.replace("\\", "/")).name != actuators.name:
            continue
        result = bpy.ops.wm.lib_relocate(
            library=library.name,
            filepath=str(actuators),
            directory=f"{actuators.parent}/",
            filename=actuators.name,
            relative_path=False,
        )
        if result != {"FINISHED"}:
            raise RuntimeError(f"Could not relocate {library.name}: {result}")

    if not hasattr(bpy.types.WindowManager, "ar_mech_enable"):
        raise RuntimeError("The mechanism extension did not remain registered")

    select_collisions()

    missing_targets = [
        obj.name_full
        for obj in bpy.context.scene.objects
        if obj.ar_props.is_target and obj.ar_props.target_obj is None
    ]
    if missing_targets:
        raise RuntimeError(f"Mechanism targets have no target object: {missing_targets}")

    simulation = mechanism.new_simulation()
    simulation.build_model(
        bpy.data.filepath,
        bpy.context.scene.collection,
        **bpy.context.scene.ar_props,
    )
    result = bpy.ops.export_scene.mujoco_usd(filepath=str(output))
    if result != {"FINISHED"}:
        raise RuntimeError(f"USD export failed: {result}")


def run_export() -> None:
    try:
        export()
    except Exception:
        traceback.print_exc()
        status.write_text("failed\n")
    else:
        status.write_text("succeeded\n")
    bpy.ops.wm.quit_blender()


mechanism.register()
print("Registered mechanism before opening the blend file", flush=True)
status.unlink(missing_ok=True)
bpy.app.timers.register(run_export, first_interval=0.25, persistent=True)
```

Launch Blender without a positional blend-file argument:

```bash
blender --python /path/to/export_v5_p2_usd.py
cat "$EXPORT_STATUS_FILE"
rm "$EXPORT_STATUS_FILE"
```

Blender can crash during scripted shutdown after a successful foreground
export. A `succeeded` status is necessary but not sufficient. Validate the
written stage before using it:

```bash
cd "$ALF_REPO"
uv run --group rl python - <<'PY'
import os

from pxr import Usd

stage = Usd.Stage.Open(os.environ["USD_FILE"])
assert stage
assert stage.GetDefaultPrim().GetPath() == "/digit_v5_p2"
for body in (
    "left_arm_finger_passive",
    "left_arm_finger_active",
    "right_arm_finger_passive",
    "right_arm_finger_active",
):
    assert stage.GetPrimAtPath(f"/digit_v5_p2/{body}")
print("USD stage is valid")
PY
```

Inspect every changed file in the asset directory. The exporter can update the
root layer, binary mesh layer, and companion layers:

```bash
git -C "$ALF_REPO" status --short \
  src/agility/core/models/usd/digit-v5-p2
```

## Build ar-control

Build the narrow simulator target from the `agility-software` checkout:

```bash
cd "$AGILITY_SOFTWARE_REPO"
bazel build //package/ar-control:ar-control
export AR_CONTROL_PATH="$(
  bazel info bazel-bin
)/package/ar-control/ar-control"
test -x "$AR_CONTROL_PATH"
```

Using `bazel info bazel-bin` avoids assumptions about the repository location
or Bazel output-directory layout.

## Run the kinematic check

Run the canonical checker from the ALF checkout. The articulated-finger option
selects the exported active-finger joints instead of the fixed-finger variant.

```bash
cd "$ALF_REPO"
uv run --group rl python -u -m agility.rl.scripts.kinematic_check \
  --robot-type digit-v5-p2 \
  --use-articulated-finger-active true \
  --ignore-soft-limit-violations-on left_leg_knee right_leg_knee
```

The check starts the binary named by `AR_CONTROL_PATH`, loads the ALF USD
assets, and compares body poses, joint axes in world and body frames, joint
hard and bounded soft limits, body masses, inertia tensors, and total mass. The
knee exceptions preserve ALF's intentionally more permissive soft upper bound;
the checker still prints those two mismatches as ignored.

Success requires an exit status of zero and no unignored `mismatch` lines.
Messages that skip axes for fixed bodies, or skip the root-body mass and inertia,
are expected. Any `Model equivalence check failed` message is a real failure.

Keep the ALF and `agility-software` branches compatible. If a robot-type name,
body name, or model layout changes in one repository, update the corresponding
checker mapping rather than adding a local wrapper around the checker.

## Common failures

- **The extension is absent after the file opens:** register `mechanism` before
  calling `bpy.ops.wm.open_mainfile`.
- **An actuator library is missing:** confirm `ACTUATORS_BLEND` names the
  `actuators.blend` file from the same `blender-data` checkout.
- **The wrong collision set is present:** set `COLLISION_SET` to `detailed` or
  `minimal` and confirm the expected collection-count assertion passes.
- **The export operator is unavailable:** build the mechanism simulation before
  calling `bpy.ops.export_scene.mujoco_usd`.
- **A modal export reports that `context.window` is missing:** use the
  foreground mechanism script above instead of launching a UI operator before
  Blender has created a window.
- **The checker downloads ar-control:** `AR_CONTROL_PATH` is unset or was not
  exported into the checker process.
- **The checker uses fixed fingers:** pass
  `--use-articulated-finger-active true`.
