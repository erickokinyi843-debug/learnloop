const configuredApiOrigin = process.env.LEARNLOOP_API_ORIGIN?.trim();

if (!configuredApiOrigin) {
  throw new Error("Set LEARNLOOP_API_ORIGIN to the HTTPS origin of the Learnloop backend.");
}

let apiOrigin;
try {
  apiOrigin = new URL(configuredApiOrigin);
} catch {
  throw new Error("LEARNLOOP_API_ORIGIN must be a valid HTTPS origin.");
}

if (
  apiOrigin.protocol !== "https:" ||
  apiOrigin.username ||
  apiOrigin.password ||
  apiOrigin.pathname !== "/" ||
  apiOrigin.search ||
  apiOrigin.hash
) {
  throw new Error("LEARNLOOP_API_ORIGIN must be an HTTPS origin without a path or credentials.");
}

export const config = {
  buildCommand: "python build_vercel.py",
  outputDirectory: "dist",
  rewrites: [
    {
      source: "/api/:path*",
      destination: `${apiOrigin.origin}/api/:path*`,
    },
    {
      source: "/admin",
      destination: "/index.html",
    },
    {
      source: "/admin/",
      destination: "/index.html",
    },
  ],
  headers: [
    {
      source: "/(.*)",
      headers: [
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        {
          key: "Content-Security-Policy",
          value:
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-src https://www.youtube.com https://www.youtube-nocookie.com https://player.vimeo.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        },
      ],
    },
    {
      source: "/api/:path*",
      headers: [{ key: "x-vercel-enable-rewrite-caching", value: "0" }],
    },
  ],
};
