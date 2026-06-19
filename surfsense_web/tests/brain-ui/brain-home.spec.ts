import { expect, test } from "@playwright/test";

/**
 * F6.13 — Brain UI E2E: Home
 *
 * Verifica que la página de inicio de SecondBrainSense carga correctamente
 * para un usuario autenticado y muestra los elementos clave de la UI.
 *
 * No depende de datos reales en Qdrant — solo comprueba que la UI renderiza
 * sin errores de JavaScript y muestra los elementos estructurales.
 */
test.describe("Brain Home", () => {
	test("carga el dashboard Brain para un usuario autenticado", async ({ page }) => {
		// Navegar directo al brain home del search space 1 (seeded en e2e)
		await page.goto("/dashboard/1/brain/home");

		// El título debe estar presente
		await expect(page.getByRole("heading", { name: /SecondBrainSense/i })).toBeVisible({
			timeout: 30_000,
		});
	});

	test("muestra el breadcrumb con enlace a Home", async ({ page }) => {
		await page.goto("/dashboard/1/brain/home");

		// El breadcrumb de Brain está en un <nav aria-label>
		const breadcrumb = page.getByRole("navigation", { name: /breadcrumb/i });
		await expect(breadcrumb).toBeVisible({ timeout: 30_000 });
		await expect(breadcrumb.getByText("SecondBrainSense")).toBeVisible();
	});

	test("muestra la sección de accesos rápidos con los 7 destinos", async ({ page }) => {
		await page.goto("/dashboard/1/brain/home");

		// Esperar a que cargue el contenido
		await page.waitForLoadState("networkidle");

		const section = page.getByRole("region", { name: /accesos rápidos/i });
		await expect(section).toBeVisible({ timeout: 30_000 });

		// Debe haber exactamente 7 links (Chat, Wiki, Grafo, Ingestar, Métricas, Admin, Maestros)
		const links = section.getByRole("link");
		await expect(links).toHaveCount(7);
	});

	test("muestra la sección de estado de servicios", async ({ page }) => {
		await page.goto("/dashboard/1/brain/home");

		await expect(page.getByText(/Estado de servicios/i)).toBeVisible({ timeout: 30_000 });
	});

	test("no tiene errores de consola JavaScript", async ({ page }) => {
		const errors: string[] = [];
		page.on("console", (msg) => {
			if (msg.type() === "error") errors.push(msg.text());
		});

		await page.goto("/dashboard/1/brain/home");
		await page.waitForLoadState("networkidle");

		// Filtrar errores esperables de red (backend puede no estar activo en CI)
		const criticalErrors = errors.filter(
			(e) =>
				!e.includes("Failed to fetch") &&
				!e.includes("NetworkError") &&
				!e.includes("net::ERR"),
		);
		expect(criticalErrors).toHaveLength(0);
	});
});
