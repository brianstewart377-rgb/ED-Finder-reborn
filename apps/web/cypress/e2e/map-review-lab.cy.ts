import { GALAXY_REGION_NAMES } from '../../src/lib/spatial/galaxy-regions';

function captureSubmittedBabylonFrame(name: string): void {
  let screenshotPath = '';
  cy.get<HTMLCanvasElement>('.spatial-canvas canvas')
    .should('be.visible')
    .screenshot(name, {
      overwrite: true,
      // Keep demand-render scheduling live while Cypress scrolls/captures.
      disableTimersAndAnimations: false,
      onAfterScreenshot(_element, props) {
        screenshotPath = props.path;
      },
    });
  // Inspect the compositor capture, not drawImage(WebGPUCanvas): the latter
  // can return empty pixels after presentation even while the displayed frame is
  // correct. The canvas-only image excludes headings, controls and status text.
  cy.then(() => cy.readFile<string>(screenshotPath, 'base64')).then(
    async (base64) => {
      const capturedFrame = new Image();
      await new Promise<void>((resolve, reject) => {
        capturedFrame.onload = () => resolve();
        capturedFrame.onerror = () => reject(new Error('Invalid canvas PNG'));
        capturedFrame.src = `data:image/png;base64,${base64}`;
      });
      const probe = document.createElement('canvas');
      probe.width = 128;
      probe.height = 64;
      const context = probe.getContext('2d');
      expect(context, '2-D visual probe').not.to.equal(null);
      context!.drawImage(capturedFrame, 0, 0, probe.width, probe.height);
      const pixels = context!.getImageData(
        0,
        0,
        probe.width,
        probe.height,
      ).data;
      let visibleSignalPixels = 0;
      for (let index = 0; index < pixels.length; index += 4) {
        if (pixels[index]! + pixels[index + 1]! + pixels[index + 2]! > 40) {
          visibleSignalPixels += 1;
        }
      }
      expect(
        visibleSignalPixels,
        'non-background pixels in the captured Babylon frame',
      ).to.be.greaterThan(10);
    },
  );
}

describe('Galaxy Map Review Lab density truth slice', () => {
  for (const backend of ['WEBGPU', 'WEBGL2'] as const) {
    for (const [width, height] of [
      [1280, 720],
      [1440, 900],
    ] as const) {
      it(`renders and navigates factual layers on ${backend} at ${width}×${height}`, () => {
        cy.viewport(width, height);
        cy.visit('/map-review-lab', {
          onBeforeLoad(window) {
            if (backend === 'WEBGL2') {
              // Exercise the ordinary capability fallback, keeping the same scene.
              Object.defineProperty(window.navigator, 'gpu', {
                value: undefined,
                configurable: true,
              });
            }
          },
        });

        cy.contains('NON-PRODUCT · deterministic fixture').should('be.visible');
        cy.contains('h2', 'Known systems density').should('be.visible');
        cy.get('[data-density-source-count]').then(($source) => {
          cy.get('[data-density-covered-count]').should(
            'have.text',
            $source.text().trim(),
          );
        });
        cy.get('[role="status"][data-renderer-state="ready"]', {
          timeout: 20_000,
        })
          .should('be.visible')
          .and('have.attr', 'data-renderer-backend', backend);
        // Backend readiness precedes asynchronous material/shader readiness.
        cy.get('.spatial-canvas', { timeout: 20_000 })
          .should('have.attr', 'data-applied-scene-revision', '2')
          .and('have.attr', 'data-rendered-layer-ids')
          .and('contain', 'catalogue-density')
          .and('contain', 'galaxy-regions');
        cy.get('[data-region-count]').should('have.text', '42/42');
        cy.get('[data-region-boundary-count]').should('contain.text', '22,595');
        cy.contains('Hover illuminates the complete exact').should(
          'be.visible',
        );
        cy.get('#region-picker option').should('have.length', 43);
        cy.contains('Ambient layer: off').should('be.visible');
        cy.get('[data-density-cell-count]').should('not.have.text', '0');
        cy.get('[data-map-label-kind="system"]')
          .should('have.length', 3)
          .and('contain.text', 'Fixture Core');
        cy.document().then((document) => {
          expect(document.documentElement.scrollWidth).to.equal(
            document.documentElement.clientWidth,
          );
        });
        cy.screenshot(
          `map-review-lab/galaxy-truth-${backend}-${width}x${height}`,
          {
            capture: 'viewport',
            overwrite: true,
            disableTimersAndAnimations: false,
          },
        );
        cy.get('.map-panel').scrollIntoView().should('be.visible');
        captureSubmittedBabylonFrame(
          `map-review-lab/babylon-density-${backend}-${width}x${height}`,
        );
        cy.get('.spatial-canvas')
          .invoke('attr', 'data-camera-revision')
          .then((cameraRevision) => {
            cy.contains('button', 'Simulate 6 new discoveries').click();
            cy.get('[data-density-generation]').should(
              'have.attr',
              'data-density-generation',
              'fixture:galaxy-review-v2',
            );
            cy.get('[data-density-source-count]').should('have.text', '40');
            cy.get('.spatial-canvas')
              .should('have.attr', 'data-applied-contribution-revision', '2')
              .and('have.attr', 'data-applied-scene-revision', '2')
              .and('have.attr', 'data-camera-revision', cameraRevision);
            cy.get('[data-region-count]').should('have.text', '42/42');
          });
        captureSubmittedBabylonFrame(
          `map-review-lab/babylon-density-refresh-${backend}-${width}x${height}`,
        );
        cy.get<HTMLCanvasElement>('.spatial-canvas canvas').then(($canvas) => {
          const bounds = $canvas[0]!.getBoundingClientRect();
          cy.wrap($canvas)
            .trigger('pointermove', {
              pointerType: 'mouse',
              clientX: bounds.left + bounds.width * 0.25,
              clientY: bounds.top + bounds.height * 0.7,
              force: true,
            })
            .wait(100);
        });
        cy.get('.spatial-canvas')
          .invoke('attr', 'data-last-hovered-region-id')
          .should('match', /^\d+$/)
          .then((regionId) => {
            cy.wrap(regionId).as('hoveredRegionId');
          });
        captureSubmittedBabylonFrame(
          `map-review-lab/babylon-region-hover-${backend}-${width}x${height}`,
        );
        cy.get<string>('@hoveredRegionId').then((regionId) => {
          cy.get('#region-picker').select(regionId!);
          cy.get('[data-selected-region-id]')
            .should('have.attr', 'data-selected-region-id', regionId)
            .and('not.have.text', 'None');
        });
        cy.get('.spatial-canvas').should(
          'have.attr',
          'data-applied-scene-revision',
          '3',
        );
        cy.contains('button', 'Top down').click();
        cy.get('.spatial-canvas').should(
          'have.attr',
          'data-camera-pitch',
          String(Math.PI / 2),
        );
        cy.get('[aria-label="Rotate right"]').click();
        cy.get('.spatial-canvas')
          .invoke('attr', 'data-camera-bearing')
          .should((bearing) => {
            expect(Number(bearing)).to.be.closeTo(0.25, 0.001);
          });
        cy.contains('button', 'All 42 regions').click();
        cy.get('[data-camera-distance]').should(($distance) => {
          expect(
            Number($distance.attr('data-camera-distance')),
          ).to.be.greaterThan(100_000);
        });
        cy.get('[data-grid-step-ly]')
          .invoke('attr', 'data-grid-step-ly')
          .then((step) => cy.wrap(Number(step)).as('wideGridStepLy'));
        cy.wait(750);
        cy.get('.spatial-canvas')
          .invoke('attr', 'data-camera-bearing')
          .should((bearing) => {
            expect(Number(bearing)).to.be.closeTo(0, 0.001);
          });
        cy.get('[data-map-label-kind="region"]')
          .should('have.length', 42)
          .then(($labels) => {
            const names = $labels
              .toArray()
              .map((label) => label.textContent?.trim())
              .sort();
            expect(names).to.deep.equal([...GALAXY_REGION_NAMES].sort());
          });
        cy.get('[data-atlas-region-leader-count]').should(
          'have.attr',
          'data-atlas-region-leader-count',
          '42',
        );
        cy.get('#region-picker').select('');
        cy.get('.spatial-canvas').should(
          'have.attr',
          'data-applied-scene-revision',
          '4',
        );
        cy.get<HTMLCanvasElement>('.spatial-canvas canvas').then(($canvas) => {
          const bounds = $canvas[0]!.getBoundingClientRect();
          cy.wrap($canvas)
            .trigger('pointermove', {
              pointerType: 'mouse',
              clientX: bounds.left + bounds.width * 0.64,
              clientY: bounds.top + bounds.height * 0.34,
              force: true,
            })
            .wait(100);
        });
        cy.get('.spatial-canvas')
          .invoke('attr', 'data-last-hovered-region-id')
          .should('match', /^\d+$/);
        captureSubmittedBabylonFrame(
          `map-review-lab/babylon-region-overview-hover-${backend}-${width}x${height}`,
        );
        cy.get('.spatial-canvas canvas').trigger('pointerleave', {
          pointerType: 'mouse',
          force: true,
        });
        captureSubmittedBabylonFrame(
          `map-review-lab/babylon-regions-${backend}-${width}x${height}`,
        );
        cy.contains('button', 'Fixture neighbourhood').click();
        cy.get('[data-camera-distance]').should(($distance) => {
          expect(Number($distance.attr('data-camera-distance'))).to.be.closeTo(
            2300,
            0.01,
          );
        });
        cy.get<number>('@wideGridStepLy').then((wideGridStepLy) => {
          cy.get('[data-grid-step-ly]').should(($grid) => {
            expect(Number($grid.attr('data-grid-step-ly'))).to.be.lessThan(
              wideGridStepLy,
            );
          });
        });
        cy.get('.spatial-canvas canvas').click('center');
        cy.focused()
          .should('have.class', 'spatial-canvas')
          .then(($host) => {
            $host[0]!.dispatchEvent(
              new KeyboardEvent('keydown', {
                key: 'e',
                code: 'KeyE',
                bubbles: true,
                cancelable: true,
              }),
            );
          });
        cy.get('.spatial-canvas')
          .invoke('attr', 'data-camera-bearing')
          .should((bearing) => {
            expect(Number(bearing)).to.be.closeTo(0.12, 0.001);
          });
        cy.get('[role="status"][data-renderer-state="ready"]').should(
          'be.visible',
        );
      });
    }
  }

  for (const backend of ['WEBGPU', 'WEBGL2'] as const) {
    it(`renders and navigates the S1 System Map on ${backend}`, () => {
      cy.viewport(1440, 900);
      cy.intercept('GET', '/api/v1/auth/session', {
        statusCode: 200,
        body: { authenticated: false, user: null },
      });
      // Inspect reads the generation-pinned V3 canonical routes.
      const body = (overrides: Record<string, unknown>) => ({
        source_body_id64: null,
        direct_parent_body_id: null,
        is_main_star: null,
        spectral_class: null,
        luminosity_class: null,
        is_landable: null,
        planet_mass_earth: null,
        stellar_mass_solar: null,
        surface_pressure_atm: null,
        semi_major_axis_km: null,
        lifecycle_state: 'ACTIVE',
        signals: [],
        ...overrides,
      });
      cy.intercept('GET', '/api/v1/generation', {
        statusCode: 200,
        body: {
          generation_id: 'diagnostic-generation',
          generation_key: 'diagnostic',
          publication_sequence: '1',
          published_at: '2026-10-01T00:00:00+00:00',
        },
      });
      cy.intercept('GET', '/api/v1/systems/42', {
        statusCode: 200,
        body: {
          id64: '42',
          name: 'Diagnostic Orrery',
          x_ly: 1,
          y_ly: 2,
          z_ly: 3,
          source_body_count: 3,
          loaded_body_count: 3,
          lifecycle_state: 'ACTIVE',
          source_updated_at: null,
        },
      });
      cy.intercept('GET', '/api/v1/systems/42/bodies', {
        statusCode: 200,
        body: [
          body({
            body_id: '1000',
            frontier_body_id: '0',
            name: 'Diagnostic Orrery',
            body_type: 'star',
            is_main_star: true,
            spectral_class: 'G2',
            luminosity_class: 'V',
            radius_km: 695_700,
            distance_from_arrival_ls: 0,
          }),
          body({
            body_id: '1002',
            frontier_body_id: '2',
            name: 'Diagnostic Orrery 2',
            body_type: 'planet',
            radius_km: 70_000,
            distance_from_arrival_ls: 11_450,
          }),
          body({
            body_id: '1005',
            frontier_body_id: '5',
            name: 'Diagnostic Orrery 5',
            body_type: 'planet',
            is_landable: false,
            radius_km: 6_371,
            distance_from_arrival_ls: 28_400,
            signals: [{ signal_type: 'saa_signaltype_biological', count: 4 }],
          }),
        ],
      });
      cy.intercept('GET', '/api/v1/systems/42/rings', {
        statusCode: 200,
        body: [
          {
            ring_id: '2000',
            body_id: '1002',
            source_ring_id64: null,
            kind: 'RING',
            name: 'Diagnostic Orrery 2 A Ring',
            ring_type: 'icy',
            reserve_type: null,
            inner_radius_km: 90_000,
            outer_radius_km: 130_000,
            mass_mt: null,
            signals_complete: null,
            source_updated_at: null,
            lifecycle_state: 'ACTIVE',
            signals: [],
          },
        ],
      });
      cy.intercept('GET', '/api/v1/systems/42/stations', {
        statusCode: 200,
        body: [],
      });
      // The diagnostic orrery is not a rated system; keep the panel's
      // not-rated state deterministic instead of depending on the backend.
      cy.intercept('GET', '/api/ratings/v4/systems/42', {
        statusCode: 404,
        body: {
          detail: 'System is not in the published Ratings V4 generation',
        },
      });
      cy.visit('/inspect?system=42&tab=bodies', {
        onBeforeLoad(window) {
          if (backend === 'WEBGL2') {
            Object.defineProperty(window.navigator, 'gpu', {
              value: undefined,
              configurable: true,
            });
          }
        },
      });

      cy.contains('h2', 'Explore Diagnostic Orrery').should('be.visible');
      cy.get('[role="status"][data-renderer-state="ready"]', {
        timeout: 20_000,
      }).should('have.attr', 'data-renderer-backend', backend);
      cy.get('.spatial-canvas', { timeout: 20_000 }).should(($canvas) => {
        expect($canvas).to.have.attr('data-applied-scene-revision');
        expect($canvas).to.have.attr('role', 'application');
      });
      cy.get('[aria-label="System bodies"] button').should('have.length', 3);
      cy.get('.spatial-canvas canvas').scrollIntoView();
      captureSubmittedBabylonFrame(
        `map-review-lab/system-map-${backend}-1440x900`,
      );
      cy.get('[aria-label="System bodies"] button').eq(1).click();
      cy.get('[data-selected-body-id="2"]')
        .should('contain.text', 'Diagnostic Orrery 2')
        .and('contain.text', '1 known');
      cy.get('[aria-label="Rotate right"]').click();
      cy.get('.spatial-canvas')
        .invoke('attr', 'data-camera-bearing')
        .should((bearing) => {
          expect(Number(bearing)).to.be.closeTo(-0.6, 0.001);
        });
      cy.get('.spatial-canvas canvas').scrollIntoView();
      captureSubmittedBabylonFrame(
        `map-review-lab/system-map-selected-${backend}-1440x900`,
      );
    });
  }
});
