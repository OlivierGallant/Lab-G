py# IEC 60287 Cable Layout Prototype

Prototype PySide6 application for experimenting with cable placement prior to integrating IEC 60287 thermal calculations.

## Getting Started

1. Create and activate a virtual environment (recommended).
2. Install the project in editable mode (installs dependencies and exposes the package):
   ```bash
   pip install -e .
   ```
3. Launch the application:
   ```bash
   python -m iec60287
   ```

## Current Features

- 2D placement canvas backed by `QGraphicsScene`.
- Add cables (circular items) and backfill regions (rectangular items) via the toolbar or shortcuts.
- Drag, select, and delete items; mouse-wheel zoom; middle-button pan.
- Simple grid background to help align assets.

## Next Steps

- Capture geometric and material properties for each placement item.
- Serialize layouts to JSON for later IEC 60287 calculations.
- Hook thermal calculations (T1–T4) into the model once the layout data model is stable.
