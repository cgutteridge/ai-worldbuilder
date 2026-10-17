# ai-worldbuilder

Domain language for the reusable worldbuilder tool. World-specific vocabulary lives in each world repo.

## Worlds and tools

**Worldbuilder**:
The reusable AI tool for building and refining Minecraft worlds from real-place evidence. One checkout; many worlds possible; exactly one world active at a time.
_Avoid_: refine tool, Geocraft refine, Southampton Refine (as the tool name)

**World**:
One game place under construction (offsets, gameworld id, queue, issues, feature/tree build scripts, local providers). Lives in its own repository, checked out under the worldbuilder’s gitignored `worlds/` folder.
_Avoid_: site (as the name of the whole place), project, instance (unless meaning the Minecraft launcher instance)

**Active world**:
The single world the worldbuilder is pointed at right now. Chosen by system-level configuration (`active_world.py`), not by which folder you happened to open.
_Avoid_: current cell, current queue item

**System provider**:
A reference feed whose geographic scope is nationwide or wider (UK / England or above), kept in the worldbuilder so every world can use it (`config/system-providers.json`; code under tool `providers/<id>/` when `package` is set).
_Avoid_: global source, default source

**Local provider**:
A reference feed below national scope (city, campus, single site). Kept in that world’s repository (`config/local-providers.json`; tools under world `providers/<id>/` with a catalogue `package` field). When adding a provider, ask local vs system; rule of thumb: below national → local. Loaders resolve `package` against both system and world roots (`cell_sources.provider_package_dir`).
_Avoid_: place source (as the only term)

## Trackers

**System tracker**:
Matt-style task index for worldbuilder engineering (`tasks/`). Lives only in this repo.
_Avoid_: tooling queue, QUEUE (for system work)

**World tracker**:
That world’s issue register plus ordered build queue. Never holds system engineering tasks.
_Avoid_: TODO.txt, system tasks in QUEUE

## Evidence

**Type**:
A stable class of evidence question. Named without vendors. Documented in `reference/TYPES.txt`.
_Avoid_: Source family, Google, aerial-as-vendor

**Provider**:
A feed that fills one or more types. Either a system provider or a local provider.
_Avoid_: Type, source family keyed only by vendor

**World frame**:
How real geography maps into Minecraft: CRS, offsets, Y offset, bounds, anchor. Stored in the world’s `config/world-frame.json`; read via `scripts/world_frame.py`.
_Avoid_: scattering OFFSET_E / SEA_LEVEL / gdal calls across scripts

**Anchor**:
The real-world point mapped to Minecraft (0, 0) on the horizontal plane.
_Avoid_: origin (unless meaning the MC block origin), spawn (unless meaning the player spawn)

**Y offset**:
The constant added when mapping real elevation into Minecraft Y.
_Avoid_: sea level (unless that is the explicit datum chosen)

**Empty world**:
A game save started without a prior city mesh from another generator. Refine means building from evidence and layered passes, not clearing leftover vendor lumps.
_Avoid_: Geocraft refine (as the default workflow), lump sweep (as the definition of done)

**Lidar ghost**:
Placeholder ground and grey above-ground mass in a cell, taken only from DEFRA composite terrain (DTM) and surface-minus-terrain (DSM − DTM). Exists so neighbouring cells show massing early; real ground and feature work replace it later.
_Avoid_: finished ground pass, real feature build, Geocraft lump, pristine sketch
