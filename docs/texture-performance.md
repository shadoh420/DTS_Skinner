# Texture browsing performance

The first texture-workshop build blocked game loading on full-image hue analysis and rebuilt the gallery up to three times per switch. Game loading now reads PNG dimensions from headers; full hue metadata is requested on the first explicit hue search. Unchanged gallery entries survive selection and material inspector updates.

Measured on the same machine and libraries (1,068 T1, 1,921 T2, 4,181 Q3 textures), from selecting a game until the model preview is ready:

| Game | Before, cold | After, cold | Before, warm | After, warm |
| --- | ---: | ---: | ---: | ---: |
| T1 | 2.724 s | 0.613 s | 0.411 s | 0.423 s |
| T2 | 8.212 s | 0.692 s | 0.524 s | 0.440 s |
| Q3 | 15.589 s | 1.292 s | 1.017 s | 0.807 s |

Cold means a fresh server metadata cache; the OS disk cache was not purged. These are single before/after measurements, not a general benchmark guarantee. First hue search still pays the color analysis cost. This measures game switching, not executable startup or continuous 3D rendering performance.

Validation: 53 Python tests, existing texture-workshop browser regression (transforms, original preservation, export, tags, size/hue search, undo/redo), and `tools/measure_texture_switch.cjs`. The latter checks game-ready timing, absence of eager hue requests, and retention of thumbnail DOM nodes when selecting a candidate. Run it against a freshly started server with Playwright available:

```text
node tools/measure_texture_switch.cjs http://127.0.0.1:5000
```
