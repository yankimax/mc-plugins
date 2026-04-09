import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

from PIL import Image


MODULE_PATH = pathlib.Path(__file__).resolve().parent / 'files' / 'character_preview_generator' / 'character_preview_generator.py'
SPEC = importlib.util.spec_from_file_location('character_preview_generator', MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError('failed to load character_preview_generator module')
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CharacterPreviewGeneratorTest(unittest.TestCase):
    def test_generate_skin_preview_creates_square_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app_root = pathlib.Path(tmp_dir)
            normal_dir = app_root / 'assets' / 'skins' / 'alice.image_set' / 'normal'
            normal_dir.mkdir(parents=True)
            image = Image.new('RGBA', (180, 320), (0, 0, 0, 0))
            for x in range(40, 140):
                for y in range(20, 300):
                    image.putpixel((x, y), (255, 120, 120, 255))
            image.save(normal_dir / '001.png')

            characters_dir = app_root / 'assets' / 'characters'
            characters_dir.mkdir(parents=True)
            (characters_dir / 'alice.chr').write_text(
                'meta name ru Алиса\n'
                'meta name en Alice\n'
                'gui skin alice.image_set\n',
                encoding='utf-8',
            )

            result = MODULE.generate_character_previews(app_root, size=128, force=True)

            self.assertEqual(result['generated'], 1)
            preview = app_root / 'assets' / 'skins' / 'alice.image_set' / 'preview.png'
            self.assertTrue(preview.exists())
            with Image.open(preview) as generated:
                self.assertEqual(generated.size, (128, 128))

    def test_generate_live2d_preview_prefers_icon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app_root = pathlib.Path(tmp_dir)
            live2d_root = app_root / 'assets' / 'live2d' / 'kitu_re23'
            texture_dir = live2d_root / 'KITU_RE23.4096'
            texture_dir.mkdir(parents=True)
            Image.new('RGBA', (256, 256), (0, 255, 0, 255)).save(texture_dir / 'texture_00.png')
            Image.new('RGBA', (96, 64), (255, 0, 0, 255)).save(live2d_root / 'icon.png')
            (live2d_root / 'KITU_RE23.model3.json').write_text(
                json.dumps(
                    {
                        'FileReferences': {
                            'Textures': ['KITU_RE23.4096/texture_00.png'],
                        }
                    }
                ),
                encoding='utf-8',
            )

            characters_dir = app_root / 'assets' / 'characters'
            characters_dir.mkdir(parents=True)
            (characters_dir / 'kitu.chr').write_text(
                'meta name ru Киту\n'
                'meta name en Kitu\n'
                'gui live2d kitu_re23/KITU_RE23.model3.json\n',
                encoding='utf-8',
            )

            result = MODULE.generate_character_previews(app_root, size=96, force=True)

            self.assertEqual(result['generated'], 1)
            preview = live2d_root / 'preview.png'
            with Image.open(preview) as generated:
                self.assertEqual(generated.size, (96, 96))
                bbox = generated.getbbox()
                self.assertIsNotNone(bbox)
                assert bbox is not None
                self.assertLessEqual(bbox[0], 24)
                self.assertGreaterEqual(bbox[2], 72)
                self.assertLess(bbox[3] - bbox[1], 48)

    def test_generate_spine_preview_uses_root_raster(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app_root = pathlib.Path(tmp_dir)
            spine_root = app_root / 'assets' / 'spine' / 'hero_1'
            spine_root.mkdir(parents=True)
            image = Image.new('RGBA', (320, 320), (0, 0, 0, 0))
            for x in range(110, 230):
                for y in range(30, 300):
                    image.putpixel((x, y), (20, 120, 255, 255))
            image.save(spine_root / 'Hero_1.png')
            (spine_root / 'Hero_1.skel').write_bytes(b'fake')

            characters_dir = app_root / 'assets' / 'characters'
            characters_dir.mkdir(parents=True)
            (characters_dir / 'hero_1.chr').write_text(
                'meta name ru Герой 1\n'
                'meta name en Hero 1\n'
                'gui spine hero_1/Hero_1.skel\n',
                encoding='utf-8',
            )

            result = MODULE.generate_character_previews(app_root, size=96, force=True)

            self.assertEqual(result['generated'], 1)
            preview = spine_root / 'preview.png'
            self.assertTrue(preview.exists())
            with Image.open(preview) as generated:
                self.assertEqual(generated.size, (96, 96))

    def test_missing_source_generates_placeholder_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            app_root = pathlib.Path(tmp_dir)
            characters_dir = app_root / 'assets' / 'characters'
            characters_dir.mkdir(parents=True)
            (characters_dir / 'l038.chr').write_text(
                'meta name ru L038\n'
                'meta name en L038\n'
                'gui live2d L038/l038.model3.json\n',
                encoding='utf-8',
            )

            result = MODULE.generate_character_previews(app_root, size=96, force=True)

            self.assertEqual(result['generated'], 1)
            self.assertEqual(result['generatedPlaceholders'], 1)
            preview = app_root / 'assets' / 'live2d' / 'L038' / 'preview.png'
            self.assertTrue(preview.exists())
            with Image.open(preview) as generated:
                self.assertEqual(generated.size, (96, 96))


if __name__ == '__main__':
    unittest.main()
