# Drittanbieter-Komponenten / Third-party components

Der Quellcode von FlacDup steht unter der MIT-Lizenz (siehe `LICENSE`). Der Windows-Installer
enthält zusätzlich die folgenden Bibliotheken, jeweils unter ihrer eigenen Lizenz. Ihr Quellcode
ist bei den jeweiligen Projekten erhältlich.

FlacDup's own source code is MIT licensed. The Windows installer additionally bundles the
libraries below under their own licenses; their source code is available from the upstream projects.

| Komponente | Lizenz | Projekt |
|---|---|---|
| Python 3.11 | PSF License | https://www.python.org |
| Qt 6 / PySide6, Shiboken6 | LGPL-3.0 | https://www.qt.io, https://wiki.qt.io/Qt_for_Python |
| NumPy | BSD-3-Clause | https://numpy.org |
| SciPy (inkl. OpenBLAS) | BSD-3-Clause | https://scipy.org |
| PyAV | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| FFmpeg (über die PyAV-Wheels) | LGPL-3.0-or-later; die mitgelieferten Encoder-Bibliotheken libx264/libx265 stehen unter GPL-2.0-or-later | https://ffmpeg.org |
| python-soundfile | BSD-3-Clause | https://github.com/bastibe/python-soundfile |
| libsndfile | LGPL-2.1-or-later | https://libsndfile.github.io/libsndfile/ |
| python-sounddevice | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio | MIT-artig (PortAudio License) | https://www.portaudio.com |
| mutagen | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |
| Send2Trash | BSD-3-Clause | https://github.com/arsenetar/send2trash |
| cffi | MIT | https://cffi.readthedocs.io |
| PyInstaller-Bootloader | GPL-2.0 mit Ausnahme für gebaute Programme | https://pyinstaller.org |

Da der Installer GPL-Komponenten (mutagen, libx264/libx265) enthält, wird er als Gesamtwerk unter
den Bedingungen der GPL-2.0-or-later weitergegeben. Der vollständige Quellcode von FlacDup liegt in
diesem Repository: https://github.com/Yakobo1970/FlacDup

Because the installer contains GPL components (mutagen, libx264/libx265), it is distributed as a whole
under the terms of the GPL-2.0-or-later. FlacDup's complete source code is in this repository.
