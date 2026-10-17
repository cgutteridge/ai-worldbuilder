"""Pure house-geometry helpers shared by the Broadlands Road terrace builds.

Road-facing frame: ``outward`` is the unit vector house to street in XZ, ``front`` the
front point, ``along`` the street axis.
"""
from __future__ import annotations


def perimeter(cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Footprint cells with a 4-neighbour outside the house.

    @param cells: house footprint cells
    @returns: perimeter subset
    """
    return {(x, z) for x, z in cells
            if any((x + dx, z + dz) not in cells
                   for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))}


def depth_score(cells: set[tuple[int, int]], outward: tuple[float, float],
                 front: tuple[float, float]) -> dict[tuple[int, int], float]:
    """Signed depth from the front point along −outward (into the plot).

    @returns: cell -> depth (0 at front, larger toward the back)
    """
    ox, oz = outward
    fx, fz = front
    return {(x, z): -((x + 0.5 - fx) * ox + (z + 0.5 - fz) * oz)
            for x, z in cells}


def along_score(cells: set[tuple[int, int]], along: tuple[float, float],
                 front: tuple[float, float]) -> dict[tuple[int, int], float]:
    """Coordinate along the street axis.

    @returns: cell -> along-street scalar
    """
    ax, az = along
    fx, fz = front
    return {(x, z): (x + 0.5 - fx) * ax + (z + 0.5 - fz) * az
            for x, z in cells}


def door_facing(outward: tuple[float, float]) -> str:
    """Cardinal door facing toward the road.

    @param outward: unit vector house→street in XZ
    @returns: north/south/east/west
    """
    ox, oz = outward
    if abs(oz) >= abs(ox):
        return 'south' if oz > 0 else 'north'
    return 'east' if ox > 0 else 'west'


def stair_facing_for_roof(toward_front: bool, outward: tuple[float, float]) -> str:
    """Roof stair facing (lower edge points downhill).

    @param toward_front: True if this slope faces the street
    @param outward: house→street unit vector
    @returns: facing string for stairs block
    """
    ox, oz = outward
    # Stair facing = direction the stair ascends away from… in MC, facing is
    # the direction of the lower-to-higher run (the face you walk up into).
    # For a pitch falling toward +outward, the stair faces −outward (up to ridge).
    if toward_front:
        dx, dz = -ox, -oz
    else:
        dx, dz = ox, oz
    if abs(dz) >= abs(dx):
        return 'south' if dz > 0 else 'north'
    return 'east' if dx > 0 else 'west'
