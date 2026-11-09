import type { MetadataRoute } from "next";

export const dynamic = "force-static";

// Lets phones add Musica to the home screen like an app.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Musica",
    short_name: "Musica",
    description: "Paste a link or a song name, get clean tagged MP3s on your drive.",
    start_url: "/",
    display: "standalone",
    background_color: "#0a0a0a",
    theme_color: "#0a0a0a",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
  };
}
