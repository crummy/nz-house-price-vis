# New Zealand house-price matrix

An Astro static site that shows the annualised house-price change for every
purchase and sale year. The default view adjusts prices for inflation.

## Local development

```sh
npm install
npm run dev
```

Build the static site with:

```sh
npm run build
```

Astro writes the complete site to `dist/`.

## Data

The checked-in dataset is generated from:

- The Ministry for Cities, Environment, Regions and Transport property and
  sales statistics workbook.
- The BIS nominal and real New Zealand residential property-price series.

Refresh the dataset with:

```sh
npm run data:update
```

The update script downloads the latest source files and writes
`src/data/housing.json`. It uses December observations, so the matrix ends at
the latest complete calendar year.

## Cloudflare Pages

Connect the repository to Cloudflare Pages and use these settings:

- Build command: `npm run build`
- Build output directory: `dist`
- Node.js version: `24`

The site is fully static. It needs no Functions or runtime bindings.
