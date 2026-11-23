"""Feature roof and facade details, PV arrays and clipping diagnostics."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from cellmath import DIRS
from chimney import chimney_layers
from feature_roof_zones import _bottom_slab_or_full
from feature_spec import ROOF_DETAIL_KINDS
from roof_geometry import align_stair_shapes
from roof_material import family_variants
from site_kit import AIR_IDS, bid
from sign_options import default_wood, label_sign_options, resolve_fascia_colour, sign_blocks
from stoop import FACE
from world_frame import metres_aod_to_y


if TYPE_CHECKING:
    from feature_build import Feature


_OPENING_EXTRAS = ('water', 'lava', 'iron_bars')


PV_DETECTOR = 'daylight_detector[inverted=false]'   # power follows daylight in game


class DetailPlanning:
    """Feature roof and facade details, PV arrays and clipping diagnostics.

    Used by feature_build.Build; methods share its feature-plan state.
    """

    def detail_report(self) -> list[str]:
        """Dry-run lines for each planned roof/facade detail and its source.

        @returns: printable report lines (may be empty)
        """
        lines: list[str] = []
        for f in self.features:
            for row in (self.spec.get('details') or {}).get(f.no) or []:
                ax, ay, az = row['at']
                sx, sy, sz = row['size']
                face = row.get('face')
                face_bit = f' face={face}' if face else ''
                lines.append(
                    f"detail {row['kind']} {f.no} at ({ax},{ay},{az}) "
                    f"size {sx}x{sy}x{sz}{face_bit} source={row['source']}"
                )
        return lines + self._detail_clip_lines

    def _detail_box(self, at: list | tuple, size: list | tuple):
        """Yield ``(x, y, z)`` cells in an inclusive min-corner box.

        @param at: ``[x, y, z]`` min corner
        @param size: ``[sx, sy, sz]`` extents (>=1)
        @returns: iterator of block coords
        """
        ax, ay, az = at
        sx, sy, sz = size
        for dx in range(sx):
            for dy in range(sy):
                for dz in range(sz):
                    yield (ax + dx, ay + dy, az + dz)

    def _solidify_roof_under(
        self, at: list | tuple, size: list | tuple, mat: str, supported: set[tuple[int, int]],
    ) -> None:
        """Replace slab/stair membrane under a roof detail so it does not hover.

        @param at: detail min corner
        @param size: detail size
        @param mat: roof material family or full-block material for the pad
        @param supported: roof-supported horizontal cells of this detail
        """
        ax, ay, az = at
        sx, _sy, sz = size
        mat = family_variants(mat)['full']
        for dx in range(sx):
            for dz in range(sz):
                x, z = ax + dx, az + dz
                if (x, z) not in supported:
                    continue
                below = (x, ay - 1, z)
                st = self.p.cells.get(below, '')
                name = st.split('[')[0] if st else ''
                if any(tok in name for tok in ('_slab', '_stairs')):
                    self.set(x, ay - 1, z, mat)

    def _place_detail(self, f: Feature, row: dict, roof_cells: set[tuple[int, int, int]]) -> None:
        """Place one validated detail recipe after the shell and roof.

        @param f: feature mass owning the label
        @param row: details table row
        @param roof_cells: finished shell/roof snapshot, before any details
        """
        kind = row['kind']
        at = row['at']
        size = row['size']
        face = row.get('face')
        ax, ay, az = at
        sx, sy, sz = size
        rm = f.roof.get('material') or 'smooth_stone'

        if kind == 'chimney':
            layers = chimney_layers(row)
            base = layers[0][1] | layers[0][2]
            off = sorted((x, az0) for x, az0 in base if (x, ay - 1, az0) not in roof_cells)
            if off:
                self._detail_clip_lines.append(f'detail chimney {f.no} at ({ax},{ay},{az}) cells off roof={off}; not built')
                return
            for x, z in base:
                self._solidify_roof_under((x, ay, z), (1, 1, 1), rm, {(x, z)})
            for y, ring, interior in layers:
                material = row.get('material', 'white_concrete')
                band = row.get('band') or {}
                if band.get('offset') == y - ay:
                    material = band['block']
                if y == ay + row['height'] - 1 and row.get('rim'):
                    material = row['rim']
                for x, z in ring:
                    self.set(x, y, z, material)
                for x, z in interior:
                    self.set(x, y, z, 'air')
            lidar_top = 'unavailable'
            if f.no in self.zone_data and 'dsm' in self.zone_data[f.no]:
                grid = self.zone_data[f.no]
                lidar_top = metres_aod_to_y(float(grid['dsm'][az - grid['z0'], ax - grid['x0']]))
            else:
                import lidar_provider as lp
                client = lp.tile_client(download=False)
                if not any(client.missing(product, *lp.mc_box_en(ax, az, ax, az)) for product in ('DTM', 'DSM')):
                    lidar_top = int(lp.mc_heights(client, ax, az, ax, az)['top_y'][0, 0])
            self._detail_clip_lines.append(
                f'chimney {f.no} at ({ax},{ay},{az}) planned height={row["height"]} '
                f'diameter={row["base_diameter"]}->{row["top_diameter"]} '
                f'top={ay + row["height"] - 1}; lidar top={lidar_top} (rough check only; photo/spec dimensions win)'
            )
            return

        if kind == 'skylight':
            off = [(ax + dx, ay, az + dz) for dx in range(sx) for dz in range(sz)
                   if (ax + dx, ay, az + dz) not in roof_cells
                   or any(x == ax + dx and z == az + dz and y > ay for x, y, z in roof_cells)]
            if off:
                self._detail_clip_lines.append(
                    f'detail skylight {f.no} at ({ax},{ay},{az}) cells off roof={off}; not built'
                )
                return
            for dx in range(sx):
                for dz in range(sz):
                    self.set(ax + dx, ay, az + dz, 'glass')
            return

        supported = None
        if kind in ROOF_DETAIL_KINDS:
            # Only the shell/roof can support a detail: an earlier recipe must
            # not turn an unsupported column into support for a later one.
            supported = set()
            for dx in range(sx):
                for dz in range(sz):
                    x, z = ax + dx, az + dz
                    if (x, ay - 1, z) in roof_cells:
                        supported.add((x, z))
            clipped = sx * sz - len(supported)
            if clipped:
                self._detail_clip_lines.append(
                    f'detail {kind} {f.no} at ({ax},{ay},{az}) '
                    f'unsupported cells={clipped}; clipped'
                )
            self._solidify_roof_under(at, size, rm, supported)

        def place(x, y, z, state):
            if supported is None or (x, z) in supported:
                self.set(x, y, z, state)

        if kind in ('plant_room', 'vent'):
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'gray_concrete')
            for dx in range(sx):
                for dz in range(sz):
                    place(ax + dx, ay + sy, az + dz, 'iron_trapdoor[half=bottom,open=false]')
        elif kind == 'louvred_vent':
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'iron_trapdoor[half=bottom,open=false]')
        elif kind == 'solar_panel':
            for x, y, z in self._detail_box(at, (sx, 1, sz)):
                place(x, y, z, 'black_stained_glass')
        elif kind == 'lift_overrun':
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, 'smooth_stone')
        elif kind == 'drainpipe':
            for dy in range(sy):
                place(ax, ay + dy, az, 'iron_bars')
        elif kind == 'sign':
            dx, dz = DIRS[face]
            opts = label_sign_options(self.spec, f.no)
            wood = row.get('wood') or opts.get('wood') or default_wood()
            place(ax, ay, az, 'smooth_stone')
            place(
                ax + dx, ay, az + dz,
                f"{sign_blocks(wood)['wall_sign']}[facing={FACE[face]}]",
            )
        elif kind == 'awning_like':
            dx, dz = DIRS[face]
            for i in range(max(sx, sz)):
                if face in 'NS':
                    x, z = ax + i, az
                else:
                    x, z = ax, az + i
                place(x + dx, ay, z + dz, _bottom_slab_or_full(rm))
        elif kind == 'ledge':
            for x, y, z in self._detail_box(at, (sx, 1, sz)):
                place(x, y, z, _bottom_slab_or_full(rm))
        elif kind == 'fascia_band':
            opts = label_sign_options(self.spec, f.no)
            colour = row.get('fascia_colour') or opts.get('fascia_colour')
            block = (
                f'{resolve_fascia_colour(f.no, colour)}_concrete'
                if colour is not None else 'terracotta'
            )
            for x, y, z in self._detail_box(at, size):
                place(x, y, z, block)

    def pv_rows(self) -> None:
        """Place ``roofs[label].pv_arrays`` as daylight_detector rows on the finished deck.

        A column carries a detector when it is owned by the label, lies on a row
        (every ``pitch`` blocks across the rows from the box edge), its top block
        is the label's roof material (slab or full) at the array's commonest deck
        height. Details applied afterwards win. The slab under a
        detector becomes the full roof block so the detector does not hover.

        @returns: None
        """
        self._pv_lines = []
        for f in self.features:
            rows = f.roof.get('pv_arrays') or []
            if not rows:
                continue
            mats = [f.roof.get('material') or 'smooth_stone']
            mats.extend((f.roof.get('zone_materials') or {}).values())
            full_of = {}
            for mat in mats:
                variants = family_variants(mat)
                for name in variants.values():
                    full_of[name] = variants['full']
            tops: dict[tuple[int, int], int] = {}
            for (x, y, z), state in self.p.cells.items():
                if ((x, z) in f.cells and y > tops.get((x, z), -10 ** 9)
                        and bid(state)[10:] not in AIR_IDS):
                    tops[(x, z)] = y
            for row in rows:
                x0, z0, x1, z1 = row['box']
                pitch = row.get('pitch', 2)
                along_x = row['rows'] == 'x'
                want = [
                    (x, z) for x in range(x0, x1 + 1) for z in range(z0, z1 + 1)
                    if ((z - z0) if along_x else (x - x0)) % pitch == 0
                ]
                ok: dict[tuple[int, int], int] = {}
                for c in want:
                    if c not in tops:
                        continue
                    y = tops[c]
                    name = bid(self.p.cells[(c[0], y, c[1])])[10:]
                    if name in full_of:
                        ok[c] = y
                deck = Counter(ok.values()).most_common(1)[0][0] if ok else None
                placed = 0
                for (x, z), y in sorted(ok.items()):
                    if y != deck:
                        continue
                    name = bid(self.p.cells[(x, y, z)])[10:]
                    self.set(x, y, z, full_of[name])
                    self.set(x, y + 1, z, PV_DETECTOR)
                    placed += 1
                self._pv_lines.append(
                    f'pv_array {f.no} box ({x0},{z0})..({x1},{z1}) rows={row["rows"]} pitch={pitch} '
                    f'detectors={placed} clipped={len(want) - placed} source={row["source"]}'
                )

    def details(self) -> None:
        """Place every ``details`` row after the shell and roof.

        A vent pad, chimney or skylight can replace a roof stair. Corner shapes
        are chosen again afterwards, against the stairs that remain (#390).

        @returns: None
        """
        self._detail_clip_lines = []
        # These are the full/slab/stair families used by the built structure;
        # thin roof edging (carpets/trapdoors) is deliberately not support.
        support_names = set()
        for f in self.features:
            materials = [f.roof.get('material') or 'smooth_stone', self.wall, self.floor_mat]
            materials.extend((f.roof.get('zone_materials') or {}).values())
            for material in materials:
                support_names.update(family_variants(material).values())
            edge = f.roof.get('edge') or {}
            block = bid(edge.get('block') or 'air')[10:]
            if not block.endswith(('_carpet', '_trapdoor', '_wall', '_fence', '_pane')):
                if block not in (*AIR_IDS, *_OPENING_EXTRAS):
                    support_names.add(block)
        roof_cells = {
            pos for pos, state in self.p.cells.items()
            if bid(state)[10:] in support_names or bid(state)[10:].endswith(('_slab', '_stairs'))
        }
        by_no = {f.no: f for f in self.features}
        for lab, rows in (self.spec.get('details') or {}).items():
            f = by_no.get(lab)
            if f is None:
                continue
            for row in rows:
                self._place_detail(f, row, roof_cells)
        # Details replace roof stairs, so choose corner states against the final skin.
        # Every stair is also shaped against stairs already in the world, whatever their material (#415).
        for (x, y, z), state in align_stair_shapes(
                self.p.cells, self.live.block, getattr(self.live, 'prefetch', None)).items():
            if self.p.cells.get((x, y, z)) != state:
                self.set(x, y, z, state)
