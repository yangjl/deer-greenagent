---
name: dbtl-plot-style
description: The house plot style for DBTL figures. Invoke before writing any matplotlib figure code in a Build phase, or when asked to restyle a scientific plot.
---

# DBTL plot style

Every figure a Build phase produces is read by a person deciding whether the work
is worth keeping. Raw matplotlib defaults make that read harder than it needs to
be: small labels, boxed-in axes, and a colour cycle that is not colourblind-safe.

Apply this style at the top of the entry-point script, **before any plotting**.

## The block

Paste this verbatim, immediately after the imports:

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({
    "figure.figsize": (6.4, 4.0), "figure.dpi": 150, "savefig.dpi": 150,
    "savefig.bbox": "tight", "savefig.facecolor": "white",
    "font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
    "font.size": 11, "axes.titlesize": 13, "axes.labelsize": 12,
    "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.30, "grid.linewidth": 0.6,
    "axes.axisbelow": True, "legend.frameon": False,
    "lines.linewidth": 2.0, "lines.markersize": 6,
    "patch.edgecolor": "white", "patch.linewidth": 0.5,
    "axes.prop_cycle": plt.cycler(color=[
        "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
        "#e87ba4", "#008300", "#4a3aa7", "#e34948"]),
})
```

## Rules that go with it

**Label both axes, with units.** `Grain yield (t/ha)`, not `y`. An axis a reader
has to guess at is the most common reason a good figure fails to land.

**Title says what the figure shows**, not what it is. "Response to selection over
20 generations" beats "Line plot".

**Save PNG, never PDF or SVG.** Those formats embed a creation timestamp, so the
same script produces different bytes on every run — which fails the Test stage's
byte-for-byte reproducibility check and invalidates the cycle over nothing.

**Do not change the font family.** `DejaVu Sans` ships inside matplotlib, so it
renders identically on every machine. A system font (Helvetica, Arial) is present
on some machines and not others, and the figure would stop reproducing.

**Colours in fixed order, never cycled past eight.** The cycle above is ordered so
adjacent series stay distinguishable under the common forms of colour blindness.
Past eight series, group the remainder rather than inventing a colour.

**Never rely on colour alone.** With more than two series, label them directly or
keep the legend; a reader printing in greyscale must still be able to follow.

**One message per figure.** Two unrelated measures on one plot with two y-axes is
never the answer — make two figures.

## Why it is a style block and not a config file

The style has to travel with the code. Test re-runs the recorded entry point in a
*fresh* workspace and requires every figure to match its approved hash byte for
byte. Anything set through the environment — a `matplotlibrc`, an `MPLCONFIGDIR`
file — is not carried by the script, so the re-run would render differently and
fail. Inside the script, it reproduces anywhere.
