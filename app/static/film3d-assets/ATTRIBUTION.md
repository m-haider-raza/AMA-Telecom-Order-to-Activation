# 3D film asset attribution

## Human characters

Customer: Microsoft Rocketbox `Business_Male_01`, a textured human in a business suit.
Service manager: Microsoft Rocketbox `Business_Female_02`, a distinct textured human in business clothing.

Source: https://github.com/microsoft/Microsoft-Rocketbox
Source revision: `0943055db6ec570bcef9f2c8b41c9e5467c808f9`.
Paths: `Assets/Avatars/Professions/Business_Male_01` and
`Assets/Avatars/Professions/Business_Female_02`.
Copyright (c) 2020 Microsoft. MIT license; the complete notice is retained in
`ROCKETBOX-LICENSE.md`. Original FBX meshes and skeletal rigs are retained.
The original TGA texture pixels are re-encoded losslessly as PNG for efficient
local loading. The film uses procedural skeletal poses and rendered humans,
not footage of live-action actors.

## Official Microsoft product icons

Official source and permitted architectural/training/documentation use:
https://learn.microsoft.com/en-us/azure/architecture/icons/

Archive:
https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip

The actual Azure Local, Azure Arc, Azure Container Registry and Azure Monitor
SVGs are extracted unchanged. The original SVG hashes are recorded and checked
by `scripts/Render_3D.py`. Icons remain upright, uncropped and undistorted on
camera-facing labels with the actual product names. They identify Microsoft's
products, not AMA or the agents. Microsoft trademark rights remain.
No Microsoft endorsement, partnership or compliance certification is claimed.

## Rendering dependencies and original animation

Three.js 0.180.0: https://github.com/mrdoob/three/tree/r180 (MIT).
The complete license is retained in `THREE-LICENSE.txt`; bundled third-party
notices are also emitted beside `/static/film3d.js`.
The bundled FBX decompressor uses fflate 0.8.2 (MIT), copyright (c) 2023
Arjun Barrett; its complete notice is retained in `FFLATE-LICENSE.txt`.

The tower, five agent meshes, city, stage props, event packets, camera motion
and scene staging are original procedural geometry for this demonstration.
The narration and original quiet soundscape are reused unchanged from the
preserved energetic author-voice tower edition. All voice-reference processing
remains local; no reference audio or repository content is uploaded.

Turk Telekom context branding retains the source, trademark disclaimer and
no-endorsement explanation from the previous editions' players.
