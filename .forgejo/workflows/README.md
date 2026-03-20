# Forgejo Actions Workflows

## release.yml

Automatically creates a GitHub/Forgejo release when the addon version is bumped in `addon.xml`.

### How it works

1. **Trigger**: Runs on every push to `main` branch that modifies `addon.xml`
2. **Version Detection**: Extracts the version number from `addon.xml`
3. **Change Detection**: Compares with the previous commit to detect version changes
4. **Tag Creation**: Creates a git tag `vX.Y.Z` if the version changed and tag doesn't exist
5. **Packaging**: Creates a zip file `plugin.video.tubearchivist-X.Y.Z.zip` containing:
   - All addon files
   - Excludes: `.git`, `.forgejo`, `__pycache__`, `*.pyc`, `*.yaml`, `*.md`
6. **Changelog**: Generates a changelog from git commits since the last release
7. **Release**: Creates a Forgejo release with the zip file attached

### Usage

To create a new release:

1. Edit `addon.xml` and bump the version number:
   ```xml
   <addon id="plugin.video.tubearchivist"
          name="Tube Archivist"
          version="0.4.0"
          ...>
   ```

2. Commit and push to `main`:
   ```bash
   git add addon.xml
   git commit -m "Bump version to 0.4.0"
   git push origin main
   ```

3. The workflow will automatically:
   - Create tag `v0.4.0`
   - Package the addon as `plugin.video.tubearchivist-0.4.0.zip`
   - Create a release with changelog and zip file

### Notes

- The workflow only runs when `addon.xml` is modified
- If a tag already exists for the version, the workflow skips release creation
- The zip file structure is compatible with Kodi addon installation
- Changelog is generated from git commit messages since the last tag
