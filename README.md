# Salome-Meca Contact Wizard

**Version 0.0.1**

A graphical wizard for Salome-Meca that automatically detects contacts between volume bodies and generates ready-to-use Code_Aster commands (`LIAISON_MAIL` for ties and `DEFI_CONTACT` for contacts).

Based on the original `3DContact.py` script by Lucio Gomez (psicofil).

## Features

- Automatic contact detection between selected solids using `geompy.FastIntersect`
- Every contact site between two bodies, not only the first
- A progress bar during the search
- Interactive table listing all contact pairs
- Preview of the two bodies at half transparency, with the first body's patch in red and the second body's patch in green
- The contact patch is the common overlap within the gap tolerance, including a planar slab and a curved band
- Per-contact type selection via dropdown:
  - Tie-Constraint (`LIAISON_MAIL`)
  - Contact (`DEFI_CONTACT` CONTINUE)
  - Contact with Friction
  - Ignore
- Generates a Code_Aster snippet that can be pasted into AsterStudy
- **Lineare Studie anlegen** builds a runnable linear static AsterStudy case from the selected tie rows
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
7. Click **"Code für AsterStudy generieren"** to write `aster_contacts.comm` in the current directory.
8. Copy the generated code into your AsterStudy case (into `AFFE_CHAR_MECA` for ties and/or as a `DEFI_CONTACT` definition).

For a complete linear case, select the tie rows and click **"Lineare Studie anlegen"**. The geometry must already contain groups named exactly `fixed` and `load`. The button then meshes every loaded solid, creates the case `LineareStudie`, and leaves it ready to run. It does not start the calculation and it does not save the study.

The linear case uses:

- a first-order tetrahedron mesh, maximum size 0.01, one mesh per solid, concatenated without merging nodes
- steel, `E = 2.1e11`, `NU = 0.3`
- `DDL_IMPO` on group `fixed` with `DX = DY = DZ = 0`
- `FORCE_FACE` on group `load` with `FX = 1`
- one `LIAISON_MAIL` factor per selected tie pair: slave groups are the contact patches, the master is the volume group `VOL_<i>` of the second body, `DISTANCE_MAX` is the gap tolerance
- MED output of displacements and von Mises stress (`SIEQ_NOEU`, component `VMIS`)
- 4096 MB and a time limit of 1800 s

The mesh is referenced as the live SMESH object. The result file is `linear_study/result.med` next to the opened study. A patch that does not produce a mesh group is left out and named in the dialog. On `test.hdf` at gap 0.02 that happens for `CONT_0_1_7` and `CONT_8_9_1`.

The first time AsterStudy opens, Salome may ask for the code_aster version.

## Generated Groups

The script creates one face group per contact site, named `CONT_<i>_<j>_<n>_A` and `CONT_<i>_<j>_<n>_B` (`n` counts the sites between the same two solids). Tie masters in the generated code are volume groups `VOL_<i>`, where `i` is the index of the second body in the loaded solid list. `fixed` and `load` are existing geometry groups. The linear-study button transfers them onto the mesh itself.

## Tests

`test.hdf` is the geometry fixture: 11 solids and the groups `fixed` and `load`. The tests open a copy of that file and exit Salome with `SystemExit`. They do not save `test.hdf`.

Run them from the repository root. `SALOME_MECA` may point at another starter. The command `salome` on `PATH` does not forward arguments in this environment.

```bash
testing/run_salome_test.sh contact_preview_test.py
testing/run_salome_test.sh static_analysis_test.py
testing/run_salome_test.sh linear_study_test.py
```

| Script | What it checks |
| --- | --- |
| `testing/contact_preview_test.py` | Contact search at gap 0.001 and one screenshot per site in `testing/screenshots/contact_XX.png` |
| `testing/static_analysis_test.py` | Search at gap 0.02, connectivity, tetra mesh, a code_aster solve, `displacement.png` and `von_mises.png` |
| `testing/linear_study_test.py` | The linear-study button: missing `fixed`/`load`, empty selection, then the AsterStudy case |
| `testing/shot_results.py` | Redraw the two result screenshots from an existing MED file (`CONTACT_RESULT_MED`, default `/tmp/contact_static/result.med`) |

On this fixture the gap-0.02 search finds 56 sites. The linear case keeps 54 of them and builds a mesh of 134076 nodes and 436199 tetrahedra. The solved displacement magnitude runs up to about `3.24e-8` m and the von Mises stress peaks near 2154 Pa. Reference pictures are in `testing/screenshots/`. Logs and the calculation files stay under `/tmp`.

## License

GPL-3.0 (see LICENSE file). Original contact detection logic inspired by Lucio Gomez' script (LGPL).

## Credits

- Original contact detection concept: Lucio Gomez (psicofil)
- Wizard and Code_Aster integration: developed with Grok
