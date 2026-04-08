# GitHub Pages setup for WaveHelm

This repository now includes a complete static site inside the `docs/` folder.

## What to do after pushing the repository

1. Open the repository on GitHub.
2. Go to **Settings** → **Pages**.
3. Under **Build and deployment**, choose **Deploy from a branch**.
4. Select branch **main**.
5. Select folder **/docs**.
6. Click **Save**.

GitHub Pages will then publish the site from the files already present in `docs/`.

## Expected public URLs

- Home: `https://1981-teck.github.io/WaveHelm/`
- Privacy policy: `https://1981-teck.github.io/WaveHelm/privacy-policy.html`
- License terms: `https://1981-teck.github.io/WaveHelm/license-terms.html`
- Source code and licenses: `https://1981-teck.github.io/WaveHelm/source-code-and-licenses.html`
- Support: `https://1981-teck.github.io/WaveHelm/support.html`

## Why this structure was chosen

Using `main` + `/docs` keeps the public project website in the repository itself and avoids needing a second branch only for the site.
