# Salome-Meca Contact Wizard

**Version 0.0.1**

A graphical wizard for Salome-Meca that automatically detects contacts between volume bodies and generates ready-to-use Code_Aster commands (`LIAISON_MAIL` for ties and `DEFI_CONTACT` for contacts).

Based on the original `3DContact.py` script by Lucio Gomez (psicofil).

## Features

- Automatic contact detection between selected solids using `geompy.FastIntersect`
- Interactive table listing all contact pairs
- Preview / highlight of contact faces when selecting a row
- Per-contact type selection via dropdown:
  - Tie-Constraint (`LIAISON_MAIL`)
  - Contact (`DEFI_CONTACT` CONTINUE)
  - Contact with Friction
  - Ignore
- Generates a complete Code_Aster snippet that can be pasted into AsterStudy
- Creates geometry groups automatically

## Requirements

- Salome-Meca (tested with recent versions)
- Python with PyQt5 (included in Salome)

## Installation & Usage

1. Clone or download this repository.
2. Open Salome-Meca and switch to the **Geometry** module.
3. Select the volume bodies between which you want to detect contacts (or leave none selected to try all solids).
4. Go to `File → Load Script...` and select `contact_wizard.py`.
5. Adjust the gap tolerance if needed and click **"Kontakte automatisch suchen"**.
6. Review the list, choose the desired connection type for each contact pair.
7. Click **"Code für AsterStudy generieren"**.
8. Copy the generated code into your AsterStudy case (into `AFFE_CHAR_MECA` for ties and/or as a `DEFI_CONTACT` definition).

## Generated Groups

The script creates face groups named `CONT_<i>_<j>_A` and `CONT_<i>_<j>_B` in the Geometry module. After meshing, use **Create Groups from Geometry** in the Mesh module to transfer them.

## License

GPL-3.0 (see LICENSE file). Original contact detection logic inspired by Lucio Gomez' script (LGPL).

## Credits

- Original contact detection concept: Lucio Gomez (psicofil)
- Wizard and Code_Aster integration: developed with Grok
