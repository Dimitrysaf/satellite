"""How the Presenter settings turn into the pointer, and drawing it."""

import os
import tempfile
import unittest

import cairo

from satellite.presenter_look import HIDE_AFTER, SIZES, Trail, draw_pointer, parse_color, pointer_look

WIDTH, HEIGHT = 200, 100


def render(look, x=0.5, y=0.5, trail=()):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    draw_pointer(cairo.Context(surface), WIDTH, HEIGHT, x, y, look, trail)
    surface.flush()
    return surface


def pixel(surface, x, y):
    """(r, g, b, a) bytes, alpha premultiplied as cairo stores them."""
    data, stride = surface.get_data(), surface.get_stride()
    blue, green, red, alpha = data[y * stride + x * 4:y * stride + x * 4 + 4]
    return red, green, blue, alpha


class LookTests(unittest.TestCase):
    def test_defaults(self):
        look = pointer_look({})
        self.assertEqual(look["style"], "dot")
        self.assertEqual(look["size"], SIZES["small"])
        self.assertAlmostEqual(look["opacity"], 0.8)
        self.assertEqual(look["hide_ms"], 500)
        self.assertFalse(look["trail"])

    def test_values_are_kept_in_range(self):
        look = pointer_look({"opacity": 500, "spotlight_darkness": 1, "color": "nonsense"})
        self.assertEqual(look["opacity"], 1.0)
        self.assertAlmostEqual(look["darkness"], 0.1)
        self.assertEqual(look["color"], parse_color("#e01b24", "#e01b24"))
        self.assertAlmostEqual(pointer_look({"opacity": "x"})["opacity"], 0.8)

    def test_picture_without_a_file_is_a_dot(self):
        self.assertEqual(pointer_look({"style": "image"})["style"], "dot")
        self.assertEqual(pointer_look({"style": "image", "image": "/p.png"})["style"], "image")

    def test_hide_after(self):
        self.assertEqual(pointer_look({"hide_after": "2"})["hide_ms"], 2000)
        self.assertIsNone(pointer_look({"hide_after": "never"})["hide_ms"])
        self.assertEqual(set(HIDE_AFTER), {"0.5", "2", "5", "never"})


class TrailTests(unittest.TestCase):
    def test_points_fade_then_go(self):
        trail = Trail(seconds=1.0)
        trail.add(0.1, 0.1, now=0.0)
        trail.add(0.2, 0.2, now=0.5)
        self.assertEqual([round(s, 2) for _x, _y, s in trail.faded(now=0.5)], [0.5, 1.0])
        trail.prune(now=1.2)
        self.assertEqual([(x, y) for x, y, _t in trail.points], [(0.2, 0.2)])
        trail.clear()
        self.assertEqual(trail.faded(now=1.2), [])


class DrawTests(unittest.TestCase):
    def test_dot_with_outline(self):
        look = pointer_look({"color": "#ff0000", "opacity": 100, "size": "large", "outline": "none"})
        surface = render(look)
        self.assertEqual(pixel(surface, 100, 50), (255, 0, 0, 255))
        self.assertEqual(pixel(surface, 5, 5)[3], 0)  # nothing drawn away from the dot

    def test_opacity(self):
        look = pointer_look({"color": "#ff0000", "opacity": 50, "size": "large", "outline": "none"})
        self.assertIn(pixel(render(look), 100, 50)[3], (127, 128))

    def test_spotlight_darkens_all_but_the_hole(self):
        look = pointer_look({"style": "spotlight", "spotlight_darkness": 60, "spotlight_size": "large"})
        surface = render(look)
        self.assertEqual(pixel(surface, 100, 50)[3], 0)
        self.assertEqual(pixel(surface, 2, 2)[3], 153)

    def test_spotlight_ring(self):
        look = pointer_look({"style": "spotlight", "spotlight_size": "large", "spotlight_ring": True,
                             "color": "#00ff00", "opacity": 100})
        ring_x = 100 + round(WIDTH * 0.12)
        self.assertGreater(pixel(render(look), ring_x, 50)[1], 100)

    def test_trail_is_drawn_behind_the_dot(self):
        look = pointer_look({"color": "#0000ff", "opacity": 100, "trail": True, "outline": "none"})
        without = render(look, x=0.8)
        streak = render(look, x=0.8, trail=[(0.2, 0.5, 0.9), (0.5, 0.5, 1.0)])
        self.assertEqual(pixel(without, 80, 50)[3], 0)
        self.assertGreater(pixel(streak, 80, 50)[3], 0)
        self.assertLess(pixel(streak, 80, 50)[3], 255)  # fainter than the dot

    def test_picture_pointer(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "pointer.png")
            picture = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 16, 16)
            picture.fill(0x00ff00ff)
            picture.savev(path, "png", [], [])
            look = pointer_look({"style": "image", "image": path, "opacity": 100, "size": "large"})
            self.assertEqual(pixel(render(look), 100, 50), (0, 255, 0, 255))
            missing = pointer_look({"style": "image", "image": os.path.join(folder, "gone.png"),
                                    "color": "#ff0000", "opacity": 100, "size": "large", "outline": "none"})
            self.assertEqual(pixel(render(missing), 100, 50), (255, 0, 0, 255))  # falls back to the dot


if __name__ == "__main__":
    unittest.main()
