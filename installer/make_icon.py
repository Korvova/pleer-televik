"""Иконка программы: тёмный квадрат, три дорожки каналов и кнопка «пуск». python make_icon.py icon.ico"""
import sys

from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=48, fill=(24, 33, 44, 255))
for i, color in enumerate([(47, 111, 214), (212, 65, 47), (21, 147, 90)]):
    y = 150 + i * 30
    d.rounded_rectangle((40, y, S - 40, y + 14), radius=7, fill=color + (255,))
d.ellipse((88, 40, 168, 120), fill=(255, 255, 255, 255))
d.polygon([(118, 60), (118, 100), (152, 80)], fill=(24, 33, 44, 255))
img.save(sys.argv[1] if len(sys.argv) > 1 else "icon.ico",
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
