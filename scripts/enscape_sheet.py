"""Read the user render-defaults sheet."""
import json
import os


SHEET_OMITTED_VISUAL_KEYS = frozenset({
    "ScreenshotDefaultExtension", "ScreenshotAutoFileNameEnabled", "ScreenshotAutoFolder",
    "ScreenshotUseSubFolder", "ScreenshotJsonExport",
})


def _skill_file(name):
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name)


def _read_defaults():
    """Read Value cells only; enum words such as None remain strings."""
    values = {}
    in_table = False
    with open(_skill_file("render-defaults.md"), encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.lstrip().startswith("|"):
                if in_table and line.strip():
                    raise ValueError("Invalid sheet row at line {}: {}".format(line_number, line.strip()))
                in_table = False
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells[:3] == ["Key", "Value", "Allowed"]:
                in_table = True
                continue
            if not in_table or all(set(cell) <= set("-: ") for cell in cells):
                continue
            key = cells[0]
            if len(cells) < 3 or not key or key in values:
                raise ValueError("Invalid or duplicate sheet row: " + key)
            try:
                values[key] = json.loads(cells[1])
            except ValueError:
                values[key] = cells[1]
    if not values:
        raise ValueError("The render sheet contains no setting rows")
    return values
