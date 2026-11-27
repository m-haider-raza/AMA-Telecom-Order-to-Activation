"""Serve branded editions at existing media URLs without overwriting their source films."""

from fastapi.staticfiles import StaticFiles

FILMS = (
    "current-scenario", "solution", "solution-tower-v1", "solution-3d-v1", "solution-clarity-v1",
    "solution-clarity-v2", "solution-tower-v2", "solution-tower-v3", "solution-tower-v4", "solution-tower-v5",
    "solution-tower-v6", "solution-tower-v7", "solution-tower-v8",
)
SCREENSHOTS = ("solution-intake", "solution-recovery", "solution-exception", "solution-active",
               "solution-messages", "solution-partners")
MEDIA_ALIASES = {
    name + extension: name + "-turkbiztel" + extension
    for name in FILMS for extension in (".mp4", "-poster.jpg")
}
MEDIA_ALIASES.update({name + ".jpg": name + "-turkbiztel.jpg" for name in SCREENSHOTS})
MEDIA_ALIASES.update({
    "myturkei-mark.svg": "turkbiztel-mark.svg",
    "myturkei-icons.svg": "turkbiztel-icons.svg",
})
CURRENT_SOLUTION_ALIASES = {
    edition + suffix: "solution-tower-v8" + suffix
    for edition in ("solution-tower-v6", "solution-tower-v7")
    for suffix in (".mp4", ".html", ".vtt", "-poster.jpg", "-data.json", "-transcript.txt")
}


class BrandedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        path = CURRENT_SOLUTION_ALIASES.get(path, path)
        return await super().get_response(MEDIA_ALIASES.get(path, path), scope)
