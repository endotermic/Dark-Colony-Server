Mars surface maps for tools/paint_intro.py (the main-menu planet), derived by tools/mars_maps.py
from two public-domain products (doc DC16_DISPLAY_AND_RESOLUTION.md section 10.57):

* mola_height_4096.png  - 4096 x 2048, 16-bit greyscale, metres = value * scale + offset (mars_maps.json);
                          from the MOLA MEGDR grid megt90n000eb.img, NASA PDS Geosciences Node
                          (pds-geosciences.wustl.edu/mgs/mgs-m-mola-5-megdr-l3-v1/mgsl_300x/meg016/), 16 px/degree.
* viking_color_4096.jpg - 4096 x 2048 RGB; from the USGS Astrogeology "Mars Viking Colorized Global Mosaic 232m"
                          1 km JPEG (astrogeology.usgs.gov/search/map/mars_viking_colorized_global_mosaic_232m).
* mars_maps.json        - scale / offset, sources, the detected longitude shift of the colour mosaic.

Both maps: simple cylindrical, longitude 0..360 east from the left edge, latitude +90 at the top.
Credit: NASA / JPL / GSFC (MOLA), NASA / JPL / USGS (Viking MDIM 2.1). Public domain.
Regenerate: python tools/mars_maps.py prepare <folder with megt90n000eb.img and mars_viking_mdim21_clrmosaic_1km.jpg>
