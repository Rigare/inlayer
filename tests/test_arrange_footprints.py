"""Tests for inlayer.arrange_footprints (the XY layout of several figures).

The layout works on footprints - the XY extent of everything cut for a
figure - and returns the min corner of each. Every test here uses footprints
that force the case it claims to check: an earlier version compared figures
that the packer had put into different rows, so the X-gap and ordering
asserts never ran.
"""

from __future__ import annotations

import numpy as np
import pytest

import inlayer


def _rects(sizes, positions):
    sizes = np.asarray(sizes, dtype=float)
    return [(p, p + s) for p, s in zip(np.asarray(positions), sizes)]


def _separation(a, b) -> float:
    """Distance between two rectangles along the axis that separates them."""
    return float(np.maximum(b[0] - a[1], a[0] - b[1]).max())


def _assert_all_apart(sizes, positions, gap):
    rects = _rects(sizes, positions)
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert _separation(rects[i], rects[j]) >= gap - 1e-9, (i, j, rects[i], rects[j])


class TestBasics:
    def test_single_footprint_stays_at_the_origin(self):
        np.testing.assert_allclose(inlayer.arrange_footprints([[10.0, 5.0]], 2.0), [[0.0, 0.0]])

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="At least one mesh"):
            inlayer.arrange_footprints(np.zeros((0, 2)), 2.0)

    @pytest.mark.parametrize("style", ["compact", "horizontal", "vertical"])
    def test_every_pair_keeps_the_gap(self, style):
        sizes = [[10.0, 10.0], [8.0, 12.0], [15.0, 4.0], [3.0, 3.0]]
        positions = inlayer.arrange_footprints(sizes, 3.0, style)
        _assert_all_apart(sizes, positions, 3.0)


class TestCompact:
    def test_same_row_is_exactly_gap_apart_in_x(self):
        """Two small footprints share a row: the X gap is the configured one."""
        sizes = [[10.0, 10.0], [10.0, 10.0]]
        positions = inlayer.arrange_footprints(sizes, 3.0, "compact", box_width=100.0)
        assert positions[0][1] == positions[1][1]  # really in one row
        assert abs(positions[1][0] - positions[0][0]) == pytest.approx(13.0)

    def test_rows_are_gap_apart_in_y(self):
        """A box width that fits one footprint per row forces a second row."""
        sizes = [[10.0, 10.0], [10.0, 6.0]]
        positions = inlayer.arrange_footprints(sizes, 3.0, "compact", box_width=12.0)
        assert positions[0][0] == positions[1][0] == 0.0  # really two rows
        # The second row starts one row height plus the gap further up
        assert positions[1][1] == pytest.approx(13.0)

    def test_largest_first(self):
        sizes = [[3.0, 3.0], [10.0, 10.0], [5.0, 5.0]]
        positions = inlayer.arrange_footprints(sizes, 2.0, "compact", box_width=100.0)
        assert positions[1][0] == 0.0
        assert positions[2][0] < positions[0][0]

    def test_sort_keys_override_the_footprint_area(self):
        """With the unrotated figures as keys the order survives a rotation."""
        sizes = [[3.0, 3.0], [10.0, 10.0]]
        positions = inlayer.arrange_footprints(
            sizes, 2.0, "compact", sort_keys=[200.0, 1.0], box_width=100.0
        )
        assert positions[0][0] < positions[1][0]

    def test_box_width_limits_the_rows(self):
        """Rows are packed with the full footprints (review B14).

        Packing with the bare figure widths and adding the recesses afterwards
        let a row of k figures grow 2 * (k - 1) * finger_radius past the box.
        """
        sizes = [[22.0, 12.0]] * 5
        positions = inlayer.arrange_footprints(sizes, 2.0, "compact", box_width=70.0, margin=2.0)
        right = (positions[:, 0] + 22.0).max()
        assert right <= 70.0 - 2 * 2.0 + 1e-9
        _assert_all_apart(sizes, positions, 2.0)

    def test_auto_width_is_roughly_square(self):
        sizes = [[10.0, 10.0]] * 9
        positions = inlayer.arrange_footprints(sizes, 2.0, "compact")
        span = (positions + 10.0).max(axis=0)
        assert 0.5 < span[0] / span[1] < 2.0


class TestHorizontalAndVertical:
    def test_horizontal_smallest_first_bottoms_flush(self):
        sizes = [[10.0, 10.0], [8.0, 8.0]]
        positions = inlayer.arrange_footprints(sizes, 5.0, "horizontal")
        np.testing.assert_allclose(positions[:, 1], 0.0)
        assert positions[1][0] == 0.0  # the smaller one first
        assert positions[0][0] == pytest.approx(13.0)

    def test_vertical_keeps_order_and_centres_in_x(self):
        sizes = [[10.0, 10.0], [4.0, 6.0]]
        positions = inlayer.arrange_footprints(sizes, 5.0, "vertical")
        np.testing.assert_allclose(positions[:, 0] + np.asarray(sizes)[:, 0] / 2, 0.0)
        assert positions[1][1] == pytest.approx(15.0)
