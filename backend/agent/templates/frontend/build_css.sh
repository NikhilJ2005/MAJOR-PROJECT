#!/bin/sh
# Rebuild vendor/app.css (Tailwind 3) for the generated web app. Needs Node.
cd "$(dirname "$0")"
printf '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n' > /tmp/vibestack-tw.css
npx --yes tailwindcss@3.4.17 -c tailwind.config.js -i /tmp/vibestack-tw.css -o vendor/app.css --minify
