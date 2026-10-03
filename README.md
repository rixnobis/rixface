# rixface

My face, drawn by a PlayStation. `face.svg` is the source; `svg2ps1.py face.svg > face.hh` turns it into GPU primitives (flat and Gouraud triangles, semi-transparent quads for the visor glow, lines for the traces), and `rixface.cpp` draws them with [psyqo](https://github.com/pcsx-redux/nugget). The visor snaps from amber to green and the trace lines fill in, tail last.

`screenshot.png` is a capture from a SCPH-1001.

    git submodule update --init --recursive
    make
