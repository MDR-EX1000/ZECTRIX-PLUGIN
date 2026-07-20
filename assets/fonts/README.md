# Slogan Font

`NotoSansSC-Slogan-Medium.otf` is a glyph subset of
`NotoSansSC-Medium.otf` from the official Noto CJK project. It contains
only the characters required by `DAILY_SLOGANS` in `usage_image.py`.

Source:

```text
https://github.com/notofonts/noto-cjk
```

The original OTF was obtained through Fontsource and converted/subset with
`fontTools`/`pyftsubset`. The font is distributed under the SIL Open Font
License 1.1. See `OFL.txt`.

The renderer checks fonts in this order:

1. `SLOGAN_FONT_FILE`
2. `assets/fonts/NotoSansSC-Slogan-Medium.otf`
3. `assets/fonts/msyh.ttc`
4. `assets/fonts/msyhbd.ttc`

The Microsoft YaHei files are intentionally not included. If a locally
licensed copy is available, it can be placed at one of the paths above
without changing the code.
