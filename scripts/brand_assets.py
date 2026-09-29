"""Regenerate the app icon, splash and Android icons from the mark (docs/brand.md).

uv run --with pillow python scripts/brand_assets.py
"""

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = (
	"apps/mobile/node_modules/@expo-google-fonts/instrument-serif/"
	"400Regular/InstrumentSerif_400Regular.ttf"
)
ASSETS = "apps/mobile/assets"
DEEP, MID = (11, 36, 26), (24, 66, 47)
CREAM, AMBER = (241, 236, 223), (226, 160, 74)
WHITE = (255, 255, 255, 255)
N = 2048  # drawn at 2x, downsampled for clean edges


def background(n: int = N) -> Image.Image:
	"""Deep forest with a soft light from the upper left."""
	base = Image.new("RGB", (n, n), DEEP)
	glow = Image.new("L", (n, n), 0)
	draw = ImageDraw.Draw(glow)
	cx, cy = n * 0.28, n * 0.22
	for r in range(n, 0, -8):
		draw.ellipse(
			[cx - r, cy - r, cx + r, cy + r], fill=int(255 * (1 - r / n) ** 1.6)
		)
	soft = glow.filter(ImageFilter.GaussianBlur(n * 0.03))
	return Image.composite(Image.new("RGB", (n, n), MID), base, soft)


def mark(
	canvas: Image.Image,
	scale: float,
	color: tuple[int, ...] = CREAM,
	dot: tuple[int, ...] = AMBER,
) -> None:
	"""The `m.` mark, centred; scale is the font size as a fraction of the width."""
	n = canvas.size[0]
	draw = ImageDraw.Draw(canvas)
	font = ImageFont.truetype(FONT, int(n * scale))
	left, top, right, bottom = draw.textbbox((0, 0), "m", font=font)
	radius = n * scale * 0.068
	gap = n * scale * 0.05
	total = (right - left) + gap + 2 * radius
	x = (n - total) / 2 - left
	y = (n - (bottom - top)) / 2 - top - n * 0.02
	draw.text((x, y), "m", font=font, fill=color)
	cx = x + right + gap + radius
	cy = y + bottom - radius
	draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=dot)


def transparent() -> Image.Image:
	return Image.new("RGBA", (N, N), (0, 0, 0, 0))


def save(img: Image.Image, name: str, size: int = 1024) -> None:
	img.resize((size, size), Image.Resampling.LANCZOS).save(f"{ASSETS}/{name}")


def main() -> None:
	icon = background()
	mark(icon, 0.68)
	save(icon, "icon.png")  # iOS and store icon: opaque
	save(icon, "favicon.png", 48)

	splash = transparent()
	mark(splash, 0.62)
	save(splash, "splash-icon.png")  # on the splash background colour

	# Android adaptive icon: the mark inside the 66% safe zone.
	foreground = transparent()
	mark(foreground, 0.44)
	save(foreground, "android-icon-foreground.png")
	Image.new("RGB", (1024, 1024), DEEP).save(f"{ASSETS}/android-icon-background.png")
	mono = transparent()
	mark(mono, 0.44, color=WHITE, dot=WHITE)
	save(mono, "android-icon-monochrome.png")
	print("brand assets written")


if __name__ == "__main__":
	main()
