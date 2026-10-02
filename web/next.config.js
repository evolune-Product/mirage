// NEXT_DIST lets several dev servers run from this folder without clobbering each other's build cache.
module.exports = { reactStrictMode: true, distDir: process.env.NEXT_DIST || ".next" };
