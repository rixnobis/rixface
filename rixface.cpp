// Rix's face, rix-face-v2.svg converted to GPU primitives by svg2ps1.py.
// Light system: visor snaps amber -> green (binary), trace lines fill in
// one at a time (a progression), then the cycle resets.

#include "common/syscalls/syscalls.h"
#include "face.hh"
#include "psyqo/application.hh"
#include "psyqo/gpu.hh"
#include "psyqo/primitives/control.hh"
#include "psyqo/primitives/quads.hh"
#include "psyqo/primitives/triangles.hh"
#include "psyqo/scene.hh"

namespace {

class RixFace final : public psyqo::Application {
    void prepare() override;
    void createScene() override;
};

class FaceScene final : public psyqo::Scene {
    void frame() override;
    int m_lastStage = -1;
};

RixFace rixFace;
FaceScene faceScene;

psyqo::Color packed(uint32_t p) {
    psyqo::Color c;
    c.packed = p;
    return c;
}

psyqo::Vertex vtx(int16_t x, int16_t y) { return {{.x = x, .y = y}}; }

void drawTri(const FaceTri& t) {
    psyqo::Prim::GouraudTriangle p;
    p.pointA = vtx(t.ax, t.ay);
    p.pointB = vtx(t.bx, t.by);
    p.pointC = vtx(t.cx, t.cy);
    p.setColorA(packed(t.ca)).setColorB(packed(t.cb)).setColorC(packed(t.cc));
    rixFace.gpu().sendPrimitive(p);
}

void drawFlatTri(const FaceTri& t, uint32_t color, bool semi) {
    psyqo::Prim::Triangle p(packed(color));
    p.pointA = vtx(t.ax, t.ay);
    p.pointB = vtx(t.bx, t.by);
    p.pointC = vtx(t.cx, t.cy);
    if (semi) p.setSemiTrans();
    rixFace.gpu().sendPrimitive(p);
}

void drawQuad(const FaceQuad& q, uint32_t color, bool semi) {
    psyqo::Prim::Quad p(packed(color));
    p.pointA = vtx(q.ax, q.ay);
    p.pointB = vtx(q.bx, q.by);
    p.pointC = vtx(q.cx, q.cy);
    p.pointD = vtx(q.dx, q.dy);
    if (semi) p.setSemiTrans();
    rixFace.gpu().sendPrimitive(p);
}

template <unsigned N>
constexpr unsigned count(const FaceTri (&)[N]) {
    return N;
}

constexpr uint32_t c_traceLit = 0x6ae846;
constexpr uint32_t c_traceDim = 0x3a5a2e;
constexpr uint32_t c_traceGlowColor = 0x1c4a20;
constexpr uint32_t c_haloAmber = 0x041a2c;
constexpr uint32_t c_haloGreen = 0x10340a;

// Timeline, in frames at the display rate: idle, then one trace every step,
// visor locks green on the third lit trace, hold, reset.
constexpr unsigned c_idleSeconds = 2;
constexpr unsigned c_stepTenths = 4;
constexpr unsigned c_holdSeconds = 5;
constexpr unsigned c_lockAt = 3;

}  // namespace

void RixFace::prepare() {
    psyqo::GPU::Configuration config;
    config.set(psyqo::GPU::Resolution::W320)
        .set(psyqo::GPU::VideoMode::AUTO)
        .set(psyqo::GPU::ColorMode::C15BITS)
        .set(psyqo::GPU::Interlace::PROGRESSIVE);
    gpu().initialize(config);
}

void RixFace::createScene() {
    ramsyscall_printf("RIXFACE start tris=%u traces=%u\n", count(c_faceTris), c_traceCount);
    pushScene(&faceScene);
}

void FaceScene::frame() {
    auto& gpu = rixFace.gpu();
    unsigned rate = gpu.getRefreshRate();
    unsigned step = rate * c_stepTenths / 10;
    unsigned idle = rate * c_idleSeconds;
    unsigned cycle = idle + step * c_traceCount + rate * c_holdSeconds;
    unsigned t = gpu.getFrameCount() % cycle;
    unsigned lit = t < idle ? 0 : (t - idle) / step + 1;
    if (lit > c_traceCount) lit = c_traceCount;
    bool green = lit >= c_lockAt;

    int stage = lit == 0 ? 0 : !green ? 1 : lit < c_traceCount ? 2 : 3;
    if (stage != m_lastStage) {
        static const char* const names[] = {"idle", "catching", "locked-in", "vindicated"};
        ramsyscall_printf("RIXFACE stage=%s frame=%u\n", names[stage], gpu.getFrameCount());
        m_lastStage = stage;
    }

    gpu.clear(packed(c_faceBg));

    // Dithering on for the Gouraud plates, additive blending for every
    // semi-transparent primitive below (the glows).
    psyqo::Prim::TPage tpage;
    tpage.attr.setDithering(true).set(psyqo::Prim::TPageAttr::FullBackAndFullFront).enableDisplayArea();
    gpu.sendPrimitive(tpage);

    for (auto& op : c_faceOps) {
        if (op.kind == 0) {
            drawTri(c_faceTris[op.index]);
        } else {
            drawQuad(c_faceQuads[op.index], c_faceQuads[op.index].c, false);
        }
    }

    for (auto& h : c_visorHalo) drawFlatTri(h, green ? c_haloGreen : c_haloAmber, true);
    if (green) {
        for (auto& v : c_visor_green) drawTri(v);
    } else {
        for (auto& v : c_visor_amber) drawTri(v);
    }

    for (unsigned i = 0; i < c_traceCount; i++) {
        auto& tr = c_traces[i];
        bool on = i < lit;
        if (on) {
            for (unsigned k = 0; k < tr.glowCount; k++) drawQuad(c_traceGlow[tr.glowStart + k], c_traceGlowColor, true);
        }
        for (unsigned k = 0; k < tr.coreCount; k++) {
            drawQuad(c_traceCore[tr.coreStart + k], on ? c_traceLit : c_traceDim, false);
        }
    }
}

int main() { return rixFace.run(); }
