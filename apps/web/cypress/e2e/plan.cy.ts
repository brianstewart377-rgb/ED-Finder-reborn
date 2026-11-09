import type {
  EconomyItemV1,
  PlanBodiesV1,
  PlanCatalogueV1,
  PlanEvaluationV1,
  PlanFacilityV1,
  PlanRequestV1,
  PlanStepV1,
  PlanSystemBodyV1,
  PortEconomyV1,
} from '../../src/lib/api/generated/types.gen';

// Ordinary success responses keep this browser journey
// independent of database seeds; all displayed budgets come from the intercepted
// evaluation contract, including the picker's evaluation preview and S5c bars.
const systemId = '9007199254740993';
const systemName = 'Planner Reference';
const primaryId = 'coriolis';
const consumerId = 'industrial-installation';
const payerId = 'mining-installation';
const systemTab = '[role="tab"][aria-controls="plan-system-layout"]';
const orderTab = '[role="tab"][aria-controls="plan-build-order"]';
const planStorageKey = 'ed_colony_projects_v1';
const reviewDialog = '[data-review-export]';

function facility(
  id: string,
  name: string,
  fields: Partial<PlanFacilityV1>,
): PlanFacilityV1 {
  return {
    id,
    name,
    category: 'installation',
    tier: 1,
    allowed_location: 'orbital',
    economy: null,
    is_port: false,
    escalating_port_tier: null,
    yellow_cp_cost: 0,
    green_cp_cost: 0,
    yellow_cp_generated: 0,
    green_cp_generated: 0,
    prerequisites: [],
    rule_ids: ['RB-0002'],
    ...fields,
  };
}

const catalogue: PlanCatalogueV1 = {
  rulebook_release: 'browser-contract-v1',
  facilities: [
    facility(primaryId, 'Coriolis Starport', {
      category: 'port',
      tier: 2,
      is_port: true,
      escalating_port_tier: 2,
      yellow_cp_cost: 3,
      green_cp_generated: 1,
    }),
    facility(consumerId, 'Industrial Installation', {
      tier: 2,
      yellow_cp_cost: 1,
      green_cp_generated: 1,
    }),
    facility(payerId, 'Mining Installation', { yellow_cp_generated: 2 }),
    // Raven uses the catalogue's real identifiers; retain the existing journey
    // fixtures above and make these two imported facilities available too.
    facility('coriolis_station', 'Coriolis Starport', {
      category: 'port',
      tier: 2,
      is_port: true,
      escalating_port_tier: 2,
    }),
    facility('orbital_installation_mining_outpost', 'Mining Outpost', {}),
    facility('planetary_port', 'Port', {
      category: 'port',
      is_port: true,
      tier: 3,
      allowed_location: 'surface',
    }),
    facility('surface_settlement_agriculture', 'Agricultural Settlement', {
      category: 'agriculture',
      allowed_location: 'surface',
    }),
    ...Array.from({ length: 12 }, (_, index) =>
      facility(
        `orbital-fixture-${index}`,
        `Research Installation ${index + 1}`,
        {},
      ),
    ),
  ],
};

function body(
  body_id: string,
  name: string,
  fields: Partial<PlanSystemBodyV1>,
): PlanSystemBodyV1 {
  return {
    body_id,
    name,
    direct_parent_body_id: null,
    body_type: 'Rocky body',
    is_main_star: false,
    is_landable: true,
    radius_km: 3200,
    surface_gravity_g: 0.5,
    surface_temperature_k: 240,
    atmosphere: 'No atmosphere',
    terraforming_state: 'Not terraformable',
    volcanism: 'No volcanism',
    distance_from_arrival_ls: 100,
    has_rings: false,
    geological_signals: 0,
    biological_signals: 0,
    signals_complete: true,
    lifecycle_state: 'ACTIVE',
    economy_kind: 'rocky_body',
    economy_parent_id: 'star-a',
    in_binary_pair: false,
    tidally_locked: false,
    volcanism_present: false,
    terraformable: false,
    ...fields,
  };
}

const bodies: PlanBodiesV1 = {
  generation_id: 'planner-browser-generation',
  publication_sequence: '1',
  system_id64: systemId,
  economy_facts_generation_id: 'planner-browser-derived',
  reserve_level: 'pristine',
  has_black_hole: false,
  has_white_dwarf: false,
  has_neutron_star: false,
  economy_notes: [],
  bodies: [
    body('star-a', `${systemName} A`, {
      body_type: 'Star',
      is_main_star: true,
      is_landable: false,
      radius_km: 600000,
      economy_kind: 'other_star',
      economy_parent_id: null,
    }),
    body('planet-1', `${systemName} A 1`, {
      direct_parent_body_id: 'star-a',
      has_rings: true,
      geological_signals: 2,
      predicted_surface_slots: 5,
      prediction_status: 'predicted',
      prediction_version: 'validated-slot-v1',
    }),
    body('cluster-2', `${systemName} A A Belt Cluster 2`, {
      body_type: 'Belt Cluster',
      direct_parent_body_id: 'star-a',
      is_landable: false,
      economy_kind: null,
    }),
    body('cluster-1', `${systemName} A A Belt Cluster 1`, {
      body_type: 'Belt Cluster',
      direct_parent_body_id: 'star-a',
      is_landable: false,
      economy_kind: null,
    }),
  ],
  belts: [
    {
      belt_id: 'belt-a',
      lifecycle_state: 'ACTIVE',
      name: `${systemName} A A Belt`,
      parent_body_id: 'star-a',
      ring_type: 'Rocky',
      reserve: 'Pristine',
      mass_mt: null,
      inner_radius_km: null,
      outer_radius_km: null,
    },
  ],
};

function economyItem(
  kind: EconomyItemV1['kind'],
  value: number,
  sourceSiteIds: string[],
  ruleIds: string[],
): EconomyItemV1 {
  return {
    kind,
    base: value,
    modifiers: [],
    floored: false,
    value,
    source_site_ids: sourceSiteIds,
    via_site_id: null,
    rule_ids: ruleIds,
    count: null,
  };
}

function evaluation(request: PlanRequestV1): PlanEvaluationV1 {
  const ids = request.sites.map((site) => site.facility_id);
  const hasConsumer = ids.includes(consumerId);
  const hasPayer = ids.includes(payerId);
  const funded = hasPayer && ids.indexOf(payerId) < ids.indexOf(consumerId);
  const preview = request.sites.some((site) =>
    site.site_id.startsWith('preview-'),
  );
  const steps = request.sites.map((site): PlanStepV1 => {
    const blocked = !preview && site.facility_id === consumerId && !funded;
    const consumer = site.facility_id === consumerId;
    const payer = site.facility_id === payerId;
    return {
      site_id: site.site_id,
      facility_id: site.facility_id,
      status: site.status,
      view: preview ? 'now' : 'after_plan',
      result: preview ? 'COUNTED' : blocked ? 'BLOCKED' : 'OK',
      counted_port_number: site.facility_id === primaryId ? 1 : null,
      cost_t2: consumer ? 1 : 0,
      cost_t3: 0,
      reward_t2: payer ? 2 : 0,
      reward_t3: consumer ? 1 : 0,
      t2_before: consumer && funded ? 2 : 0,
      t3_before: 0,
      t2_after: payer ? 2 : consumer && funded ? 1 : 0,
      t3_after: consumer && funded ? 1 : 0,
      reasons: blocked ? ['Not enough Tier 2 points before this step.'] : [],
      rule_ids: ['RB-0002'],
      payers: blocked ? [payerId] : [],
    };
  });
  // Strengths are returned shares, not weights to renormalize. The ordinary
  // primary-port case deliberately totals over 100% once its links are added.
  const ports: PortEconomyV1[] = request.sites
    .filter((site) =>
      catalogue.facilities.some(
        (entry) => entry.id === site.facility_id && entry.is_port,
      ),
    )
    .map((site) => ({
      site_id: site.site_id,
      body_ref: site.body_ref ?? null,
      role: 'main',
      supports: null,
      economies: [
        {
          name: 'Extraction',
          strength: hasPayer ? 1.5 : 1,
          items: [
            economyItem('own', 1, [site.site_id], ['RB-0010']),
            ...(hasPayer
              ? [
                  economyItem(
                    'strong_link',
                    0.5,
                    request.sites
                      .filter((entry) => entry.facility_id === payerId)
                      .map((entry) => entry.site_id),
                    ['RB-0015'],
                  ),
                ]
              : []),
          ],
        },
        ...(hasConsumer && funded
          ? [
              {
                name: 'Industrial',
                strength: 0.75,
                items: [
                  economyItem(
                    'strong_link',
                    0.75,
                    request.sites
                      .filter((entry) => entry.facility_id === consumerId)
                      .map((entry) => entry.site_id),
                    ['RB-0015'],
                  ),
                ],
              },
            ]
          : []),
      ],
    }));
  return {
    rulebook_release: catalogue.rulebook_release,
    system_id64: request.system_id64,
    now: { t2: 0, t3: 0 },
    after_plan: {
      t2: hasConsumer && funded ? 1 : hasPayer ? 2 : 0,
      t3: hasConsumer && funded ? 1 : 0,
    },
    next_port_cost: { t2: 3, t3: 6 },
    economies: {
      now: ports.filter((port) =>
        request.sites.some(
          (site) => site.site_id === port.site_id && site.status === 'complete',
        ),
      ),
      after_plan: ports,
      notes: [],
    },
    steps,
    plan_checks: [],
    summary: {
      OK: steps.filter((step) => step.result === 'OK').length,
      BLOCKED: steps.filter((step) => step.result === 'BLOCKED').length,
      NEEDS_INFO: 0,
      COUNTED: preview ? steps.length : 0,
      IGNORED: 0,
    },
    explanations: {
      'RB-0002': {
        id: 'RB-0002',
        title: 'Construction points',
        status: 'confirmed',
        statement: 'Earlier installations provide points for later builds.',
        quote: 'Tier 1 facilities generate Tier 2 points.',
        page: 4,
      },
      'RB-0010': {
        id: 'RB-0010',
        title: 'Body economies',
        status: 'confirmed',
        statement: 'Colony ports inherit economies from their local body.',
        quote: 'The local body determines the inherited economies.',
        page: 8,
      },
      'RB-0015': {
        id: 'RB-0015',
        title: 'Strong links',
        status: 'confirmed',
        statement:
          'Supporting facilities add their economies to the local port.',
        quote: 'Link strength depends on the facility tier.',
        page: 10,
      },
    },
  };
}

function installApiFixtures(evaluationDelayMs = 0): void {
  cy.intercept('GET', '/api/v1/auth/session', {
    authenticated: false,
    user: null,
    owner_claim_available: false,
  });
  cy.intercept('GET', '/api/health', {
    status: 'ok',
    database: 'connected',
    version: 'test',
    build_sha: 'planner-browser-fixture',
  });
  cy.intercept('GET', '/api/v1/generation', {
    generation_id: bodies.generation_id,
    generation_key: 'planner-browser',
    publication_sequence: '1',
    published_at: '2026-10-31T00:00:00Z',
  });
  cy.intercept('GET', `/api/v1/systems/${systemId}`, {
    id64: systemId,
    name: systemName,
    x_ly: 0,
    y_ly: 0,
    z_ly: 0,
    source_body_count: 0,
    loaded_body_count: 0,
    lifecycle_state: 'ACTIVE',
    source_updated_at: null,
  });
  for (const resource of ['bodies', 'rings', 'stations']) {
    cy.intercept('GET', `/api/v1/systems/${systemId}/${resource}`, []);
  }
  cy.intercept('GET', `/api/ratings/v4/systems/${systemId}`, {
    derived_generation_id: 'planner-browser-derived',
    publication_sequence: 1,
    canonical_generation_id: bodies.generation_id,
    canonical_publication_sequence: 1,
    scorer_version: '4.0.0',
    mechanics_version: '4.0.0',
    system_id64: systemId,
    ratings: [],
  });
  cy.intercept('GET', '/api/v1/plan/catalogue', catalogue).as('catalogue');
  cy.intercept('GET', `/api/v1/plan/systems/${systemId}/bodies`, bodies).as(
    'planBodies',
  );
  cy.intercept('POST', '/api/v1/plan/evaluate', (request) => {
    const plan = request.body as PlanRequestV1;
    expect(plan.system_id64, 'lossless system id').to.equal(systemId);
    expect(plan.bodies?.map((entry) => entry.body_ref)).not.to.include(
      'cluster-2',
    );
    request.reply({
      body: evaluation(plan),
      delay: plan.sites.some((site) => site.site_id.startsWith('preview-'))
        ? 0
        : evaluationDelayMs,
    });
  }).as('evaluate');
}

function addFacility(facilityId: string, previousSites: number): void {
  const name = catalogue.facilities.find(
    (entry) => entry.id === facilityId,
  )!.name;
  const opener = `button[aria-label="Add a build on ${systemName} A 1"]`;
  let storedPlan: string | null;
  cy.window().then((window) => {
    storedPlan = window.localStorage.getItem(planStorageKey);
  });
  cy.get('[data-plan-body="planet-1"]').find(opener).click();
  cy.get('[role="dialog"] [role="tab"]')
    .contains(/^Orbital$/)
    .click();
  cy.get('[role="dialog"] input[type="search"]').type(name);
  cy.get(`button[data-facility-id="${facilityId}"]`).click();
  cy.get(`[data-facility-preview="${facilityId}"]`)
    .should('be.visible')
    .and('contain.text', name);
  if (facilityId === payerId) {
    cy.get('[data-facility-preview]')
      .should('contain.text', 'Strong link: 50%')
      .and('contain.text', 'Mining Installation');
  }
  cy.get('[data-plan-body="planet-1"] .plan-site-row').should(
    'have.length',
    previousSites,
  );
  cy.window().should((window) => {
    expect(window.localStorage.getItem(planStorageKey)).to.equal(storedPlan);
  });
  cy.get('[data-add-to-plan]').focus().should('be.focused');
  cy.press(Cypress.Keyboard.Keys.SPACE);
  cy.get('[role="dialog"]').should('not.exist');
  cy.get('[data-plan-body="planet-1"] .plan-site-row').should(
    'have.length',
    previousSites + 1,
  );
  cy.get(opener).should('be.focused');
  cy.get('[data-plan-addition]')
    .should('have.attr', 'aria-live', 'polite')
    .and('contain.text', `${name} added to plan`);
}

describe('Colony planner browser-kept main path', () => {
  beforeEach(() => installApiFixtures());

  for (const [width, height] of [
    [1280, 900],
    [390, 844],
  ] as const) {
    it(`keeps the facility popup inside the viewport without shifting bodies at ${width}px`, () => {
      cy.viewport(width, height);
      cy.visit(`/plan?system=${systemId}`);
      cy.wait(['@catalogue', '@planBodies']);
      const opener = `button[aria-label="Add a build on ${systemName} A 1"]`;
      let bodyTop = 0;
      // Measure inside the body list: content above the pane may finish
      // loading after this point, but the popup must not move the rows.
      const offsetInTree = (body: HTMLElement) =>
        body.getBoundingClientRect().top -
        body.closest('.plan-body-tree')!.getBoundingClientRect().top;
      cy.get('[data-plan-body="planet-1"]').then(($body) => {
        bodyTop = offsetInTree($body[0]);
      });
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width + 1);
      });
      cy.get(opener).focus().click();
      cy.get('[role="dialog"]')
        .should('have.attr', 'aria-modal', 'false')
        .and('contain.text', `Add a facility on ${systemName} A 1`)
        .should(($dialog) => {
          const rect = $dialog[0].getBoundingClientRect();
          const document = $dialog[0].ownerDocument;
          const window = document.defaultView!;
          const viewport = window.visualViewport;
          const viewportLeft = viewport?.offsetLeft ?? 0;
          const viewportTop = viewport?.offsetTop ?? 0;
          const viewportWidth = Math.min(
            viewport?.width ?? window.innerWidth,
            document.documentElement.clientWidth,
          );
          const viewportHeight = Math.min(
            viewport?.height ?? window.innerHeight,
            document.documentElement.clientHeight,
          );
          expect(rect.left).to.be.at.least(viewportLeft);
          expect(rect.top).to.be.at.least(viewportTop);
          expect(rect.right).to.be.at.most(
            document.documentElement.clientWidth,
          );
          expect(rect.right).to.be.at.most(viewportLeft + viewportWidth);
          expect(rect.bottom).to.be.at.most(viewportTop + viewportHeight);
          expect($dialog.closest('[data-plan-body]')).to.have.length(0);
          if (viewportWidth < 640) {
            expect(
              Math.abs(
                (rect.left + rect.right) / 2 -
                  (viewportLeft + viewportWidth / 2),
              ),
            ).to.be.lessThan(2);
            expect(
              Math.abs(
                (rect.top + rect.bottom) / 2 -
                  (viewportTop + viewportHeight / 2),
              ),
            ).to.be.lessThan(2);
          }
        });
      cy.focused()
        .should('have.attr', 'role', 'tab')
        .and('have.text', 'Orbital');
      cy.get('[data-plan-body="planet-1"]').should(($body) => {
        expect(offsetInTree($body[0])).to.equal(bodyTop);
      });
      cy.get('[role="dialog"] [role="tabpanel"] .picker-list').should(
        ($list) => {
          expect($list[0].scrollHeight).to.be.greaterThan(
            $list[0].clientHeight,
          );
          expect(getComputedStyle($list[0]).overflowY).to.equal('auto');
        },
      );
      cy.get('[role="dialog"]')
        .contains(
          'button',
          /Previous facilities|More facilities|Next facilities/,
        )
        .should('not.exist');
      cy.injectAxe();
      cy.checkA11y('[role="dialog"]');
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width + 1);
      });
      cy.screenshot(
        `plan/facility-picker-${Cypress.browser.name}-${width}x${height}`,
        {
          capture: 'viewport',
          overwrite: true,
        },
      );
      cy.get('[role="dialog"] [role="tab"]')
        .contains(/^Surface$/)
        .click();
      cy.get('[role="dialog"] button[data-facility-id]').should(
        'have.length',
        2,
      );
      cy.checkA11y('[role="dialog"]');
      cy.get('[role="dialog"] input[type="search"]').type('Coriolis');
      cy.get('[role="dialog"]').should(
        'contain.text',
        'No matching facilities.',
      );
      cy.get('[role="dialog"] [role="tab"]')
        .contains(/^Orbital$/)
        .click();
      cy.get('[role="dialog"] button[data-facility-id]').should(
        'have.length',
        2,
      );
      let storedPlan: string | null;
      cy.window().then((window) => {
        storedPlan = window.localStorage.getItem(planStorageKey);
      });
      cy.get(`button[data-facility-id="${primaryId}"]`).focus().click();
      cy.get('[data-facility-preview]')
        .should('be.visible')
        .and('contain.text', 'Coriolis Starport')
        .and('contain.text', 'Tier 2')
        .and('contain.text', 'Orbital')
        .and('contain.text', 'Extraction')
        .and('contain.text', 'Own: 100%');
      cy.get('[data-plan-body="planet-1"] .plan-site-row').should(
        'have.length',
        0,
      );
      cy.window().should((window) => {
        expect(window.localStorage.getItem(planStorageKey)).to.equal(
          storedPlan,
        );
      });
      cy.checkA11y('[role="dialog"]');
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width + 1);
      });
      cy.screenshot(
        `plan/facility-preview-${Cypress.browser.name}-${width}x${height}`,
        { capture: 'viewport', overwrite: true },
      );
      if (width < 640) {
        cy.get('[data-facility-preview] .preview-details')
          .should(($details) => {
            expect($details[0].scrollHeight).to.be.greaterThan(
              $details[0].clientHeight,
            );
          })
          .focus()
          .should('be.focused');
        cy.press(Cypress.Keyboard.Keys.SPACE);
        cy.get('[data-facility-preview] .preview-details').should(
          ($details) => {
            expect($details[0].scrollTop).to.be.greaterThan(0);
          },
        );
      }
      cy.contains('button', 'Close preview').click();
      cy.get('[data-facility-preview]').should('not.exist');
      cy.get(`button[data-facility-id="${primaryId}"]`)
        .should('be.focused')
        .click();
      cy.press(Cypress.Keyboard.Keys.ESC);
      cy.get('[data-facility-preview]').should('not.exist');
      cy.get('[role="dialog"]').should('be.visible');
      cy.get(`button[data-facility-id="${primaryId}"]`).should('be.focused');
      cy.press(Cypress.Keyboard.Keys.ESC);
      cy.get('[role="dialog"]').should('not.exist');
      cy.get(opener).should('be.focused');
      cy.get(opener).click();
      cy.get(`button[data-facility-id="${primaryId}"]`).click();
      cy.contains('button', 'Close picker').click();
      cy.get(opener).should('be.focused');
      cy.get('[data-plan-body="planet-1"] .plan-site-row').should(
        'have.length',
        0,
      );
      cy.window().should((window) => {
        expect(window.localStorage.getItem(planStorageKey)).to.equal(
          storedPlan,
        );
      });
      cy.get(opener).click();
      cy.get('body').click(2, 2);
      cy.get('[role="dialog"]').should('not.exist');
      cy.get(opener).should('be.focused');
      cy.checkA11y();
    });

    it(`plans from Inspect, fixes build order and keeps the plan at ${width}px`, () => {
      cy.viewport(width, height);
      cy.visit(`/inspect?system=${systemId}`);
      cy.contains('h1', systemName).should('be.visible');
      cy.contains('a', 'Continue to Plan').focus();
      cy.press(Cypress.Keyboard.Keys.ENTER);
      cy.location('pathname').should('eq', '/plan');
      cy.wait(['@catalogue', '@planBodies']);
      cy.get('[data-plan-body="planet-1"]').should('be.visible');
      cy.get('[data-plan-body="cluster-1"]').should('exist');
      cy.get('[data-plan-body="cluster-2"]').should('not.exist');
      cy.get('[data-plan-body="planet-1"]')
        .find('button[aria-label^="Orbital slots on"]')
        .should('contain.text', '0/?');
      cy.get('[data-plan-body="planet-1"]')
        .find('button[aria-label^="Surface slots on"]')
        .should('contain.text', '0/5');
      cy.get('[data-plan-body="planet-1"] .slot-source').should(
        'have.text',
        'predicted',
      );

      cy.get('[data-plan-body="planet-1"]')
        .find('button[aria-label^="Orbital slots on"]')
        .click();
      cy.get('button[aria-label="Increase orbital slots"]')
        .click()
        .click()
        .click();
      cy.contains('button', 'Done').click();
      cy.get('[data-plan-body="planet-1"]').should('contain.text', '0/3');

      addFacility(primaryId, 0);
      addFacility(consumerId, 1);
      addFacility(payerId, 2);
      cy.get('[aria-live="polite"]').should('contain.text', '1 blocked');
      cy.screenshot(
        `plan/system-layout-${Cypress.browser.name}-${width}x${height}`,
        { capture: 'fullPage', overwrite: true },
      );
      // The read-only schematic marks the planned builds and never makes the
      // page itself scroll sideways.
      cy.get('[data-plan-schematic]')
        .should('be.visible')
        .and('contain.text', 'schematic not to scale');
      cy.get('[data-plan-schematic] [data-schematic-marker="true"]').should(
        'have.length.at.least',
        1,
      );
      cy.injectAxe();
      cy.checkA11y('[data-plan-schematic]');
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width + 1);
      });
      let storedBeforeTab: string | null;
      cy.window().then((window) => {
        storedBeforeTab = window.localStorage.getItem(planStorageKey);
      });
      cy.get(systemTab)
        .should('have.attr', 'aria-selected', 'true')
        .and('have.attr', 'tabindex', '0')
        .focus();
      cy.press(Cypress.Keyboard.Keys.END);
      cy.get(orderTab)
        .should('be.focused')
        .and('have.attr', 'aria-selected', 'true')
        .and('have.attr', 'tabindex', '0');
      cy.get(systemTab).should('have.attr', 'tabindex', '-1');
      cy.get('#plan-system-layout').should('have.attr', 'hidden');
      cy.get('#plan-system-layout').should('have.attr', 'inert');
      cy.get('#plan-build-order').should('be.visible');
      cy.location('search').should('include', 'tab=build-order');
      cy.window().should((window) => {
        expect(window.localStorage.getItem(planStorageKey)).to.equal(
          storedBeforeTab,
        );
      });
      cy.get('[data-plan-build-order] [data-plan-site]').should(
        'have.length',
        3,
      );
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(1)
        .should('contain.text', 'Industrial Installation')
        .and('contain.text', 'Blocked');
      cy.get('[data-plan-summary]')
        .contains('dt', /^Tier 2$/)
        .next('dd')
        .should('have.text', '2');
      cy.get('[data-plan-summary]')
        .contains('dt', /^Tier 3$/)
        .next('dd')
        .should('have.text', '0');
      cy.get('[aria-label="Points view"]').contains('button', /^Now$/).click();
      for (const tier of ['Tier 2', 'Tier 3']) {
        cy.get('[data-plan-summary]')
          .contains('dt', new RegExp(`^${tier}$`))
          .next('dd')
          .should('have.text', '0');
      }
      cy.get('[aria-label="Points view"]')
        .contains('button', 'After the plan')
        .click();
      cy.get('[data-plan-summary]')
        .contains('dt', /^Tier 2$/)
        .next('dd')
        .should('have.text', '2');
      cy.get('[data-plan-summary]')
        .contains('dt', 'Next Tier 3 port cost')
        .next('dd')
        .should('have.text', '6 T3');
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(1)
        .contains('button', 'Why?')
        .focus()
        .click();
      cy.get('[role="dialog"]')
        .should('contain.text', 'Not enough Tier 2 points')
        .and('contain.text', 'Construction points');
      cy.focused().should(($element) => {
        expect($element.closest('[role="dialog"]').length).to.eq(1);
      });
      cy.press(Cypress.Keyboard.Keys.ESC);
      cy.get('[role="dialog"]').should('not.exist');
      cy.focused().should('contain.text', 'Why?');

      cy.contains('button', /^Review & export$/)
        .focus()
        .click();
      cy.get(reviewDialog)
        .should('have.attr', 'aria-modal', 'false')
        .find('[data-review-kind="build"]')
        .contains('Industrial Installation')
        .click();
      cy.get(orderTab).should('have.attr', 'aria-selected', 'true');
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(1)
        .should('be.focused');
      cy.get(reviewDialog).should('be.visible');
      cy.get(reviewDialog)
        .contains('button', /^Close$/)
        .click();
      cy.contains('button', /^Review & export$/).should('be.focused');

      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(2)
        .find('button[aria-label^="Move up"]')
        .focus()
        .should('be.focused');
      cy.press(Cypress.Keyboard.Keys.SPACE);
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(1)
        .should('contain.text', 'Mining Installation');
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(2)
        .should('contain.text', 'Industrial Installation')
        .and('contain.text', 'OK');
      cy.get('[aria-live="polite"]').should('contain.text', '0 blocked');
      for (const tier of ['Tier 2', 'Tier 3']) {
        cy.get('[data-plan-summary]')
          .contains('dt', new RegExp(`^${tier}$`))
          .next('dd')
          .should('have.text', '1');
      }
      cy.get('[data-plan-economies] [data-economy-port]')
        .should('have.length', 1)
        .and('contain.text', 'Primary Port');
      cy.get('[data-economy-view="now"]')
        .should('contain.text', 'Now')
        .and('contain.text', 'No economy');
      cy.get('[data-economy-view="after_plan"]')
        .should('contain.text', 'After plan')
        .and('contain.text', 'Extraction')
        .and('contain.text', '150%')
        .and('contain.text', 'Industrial')
        .and('contain.text', '75%')
        .and('contain.text', '225%');
      cy.get(
        '[data-economy-view="after_plan"] button[data-economy-name="Extraction"]',
      )
        .focus()
        .should('be.focused');
      cy.get('[role="tooltip"]')
        .should('be.visible')
        .and('contain.text', 'Own')
        .and('contain.text', '100%')
        .and('contain.text', 'Strong link')
        .and('contain.text', '50%')
        .and('contain.text', 'Passed on')
        .and('contain.text', 'Planetary port')
        .and('contain.text', 'Weak link');
      cy.focused().should('have.attr', 'data-economy-name', 'Extraction');
      cy.press(Cypress.Keyboard.Keys.SPACE);
      cy.get('[role="dialog"]')
        .should('contain.text', 'RB-0010')
        .and('contain.text', 'Body economies')
        .and('contain.text', 'RB-0015')
        .and('contain.text', 'Strong links')
        .and('not.contain.text', 'RB-0002');
      cy.press(Cypress.Keyboard.Keys.ESC);
      cy.get('[role="dialog"]').should('not.exist');
      cy.focused().should('have.attr', 'data-economy-name', 'Extraction');
      cy.injectAxe();
      cy.checkA11y('[data-plan-economies]');
      cy.checkA11y();
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width);
      });
      cy.screenshot(`plan/ordered-${Cypress.browser.name}-${width}x${height}`, {
        capture: 'fullPage',
        overwrite: true,
      });

      cy.contains('button', /^Review & export$/)
        .focus()
        .click();
      cy.get(reviewDialog)
        .should('be.visible')
        .and('contain.text', systemName)
        .and('not.contain.text', 'ready to build');
      cy.checkA11y(reviewDialog);
      cy.screenshot(`plan/review-${Cypress.browser.name}-${width}x${height}`, {
        capture: 'viewport',
        overwrite: true,
      });
      // The Build order and Export tabs show their own panel and keep the
      // DRAFT export button in view.
      cy.get(`${reviewDialog} [role="tab"]`)
        .contains(/^Build order$/)
        .click();
      cy.get(`${reviewDialog} table`).should('be.visible');
      cy.get(`${reviewDialog} #plan-review-panel-overview`).should(
        'not.be.visible',
      );
      cy.checkA11y(reviewDialog);
      cy.screenshot(
        `plan/review-build-order-${Cypress.browser.name}-${width}x${height}`,
        { capture: 'viewport', overwrite: true },
      );
      cy.get(`${reviewDialog} [role="tab"]`)
        .contains(/^Export$/)
        .click();
      cy.get(reviewDialog)
        .contains('h3', 'Before you export')
        .should('be.visible');
      cy.get(reviewDialog)
        .contains('button', 'Export DRAFT plan')
        .should('be.visible');
      cy.checkA11y(reviewDialog);
      cy.screenshot(
        `plan/review-export-${Cypress.browser.name}-${width}x${height}`,
        { capture: 'viewport', overwrite: true },
      );
      cy.get(`${reviewDialog} [role="tab"]`)
        .contains(/^Overview$/)
        .click();
      cy.get(
        `${reviewDialog} [data-review-kind="unknown-slot"][data-body-ref="star-a"][data-slot-kind="orbital"]`,
      ).click();
      cy.get(systemTab).should('have.attr', 'aria-selected', 'true');
      cy.get(
        '[data-plan-body="star-a"] button[aria-label^="Orbital slots on"]',
      ).should('be.focused');
      cy.get(reviewDialog).should('be.visible');
      cy.get(
        `${reviewDialog} [data-review-kind="prediction"][data-body-ref="planet-1"]`,
      )
        .parent()
        .should('contain.text', 'predicted')
        .find('button')
        .click();
      cy.get(
        '[data-plan-body="planet-1"] button[aria-label^="Surface slots on"]',
      ).should('be.focused');

      // Await the real download before importing it. Comparing site IDs rules
      // out a stale download from the previous viewport's journey.
      const draftPath = `${Cypress.config('downloadsFolder')}/colony-plan-DRAFT-${systemId}.json`;
      // Firefox preserves existing downloads by adding a filename suffix.
      // Remove only this fixture's prior export so its exact name is tested.
      cy.exec(
        `node -e 'require("node:fs").rmSync(process.env.PLAN_DRAFT_PATH, { force: true })'`,
        { env: { PLAN_DRAFT_PATH: draftPath } },
      );
      let draftFilename = '';
      let exportedSiteIds: string[] = [];
      cy.get('[data-plan-build-order] [data-plan-site]').then(($sites) => {
        exportedSiteIds = [...$sites].map((site) => site.dataset.planSite!);
      });
      cy.window().then((window) => {
        const click = window.HTMLAnchorElement.prototype.click;
        cy.stub(window.HTMLAnchorElement.prototype, 'click').callsFake(
          function (this: HTMLAnchorElement) {
            draftFilename = this.download;
            click.call(this);
          },
        );
      });
      cy.get(reviewDialog)
        .contains('button', /^Export DRAFT plan$/)
        .should('have.attr', 'aria-label', 'Export DRAFT plan')
        .click();
      cy.then(() => {
        expect(draftFilename).to.equal(`colony-plan-DRAFT-${systemId}.json`);
        expect(draftFilename).to.contain('DRAFT');
      });
      cy.readFile(draftPath, null).should((contents) => {
        expect(contents.length, 'completed draft download').to.be.greaterThan(
          0,
        );
        const exported = JSON.parse(contents.toString());
        expect(exported.schema).to.equal('cpe_plan_export_v1');
        expect(exported.system_id64).to.equal(systemId);
        expect(
          exported.sites.map((site: { site_id: string }) => site.site_id),
        ).to.deep.equal(exportedSiteIds);
      });
      cy.get(reviewDialog)
        .contains('button', /^Close$/)
        .click();
      cy.contains('button', /^Review & export$/).should('be.focused');
      cy.get(orderTab).click();
      cy.location('search').should('include', 'tab=build-order');
      cy.window().then((window) => {
        cy.stub(window, 'confirm').as('confirmPlan').returns(true);
      });
      cy.contains('button', /^Abandon plan$/).click();
      cy.get('[data-plan-build-order] [data-plan-site]').should('not.exist');
      cy.contains('summary', /^Import$/).click();
      cy.contains('button', /^Import plan$/).click();
      cy.get('input[aria-label="Import plan file"]').selectFile(draftPath, {
        force: true,
      });
      cy.get('@confirmPlan').should('have.been.calledTwice');
      cy.get('[data-plan-build-order] [data-plan-site]').should(($sites) => {
        expect([...$sites].map((site) => site.dataset.planSite)).to.deep.equal(
          exportedSiteIds,
        );
      });
      cy.reload();
      cy.get(orderTab).should('have.attr', 'aria-selected', 'true').focus();
      cy.press(Cypress.Keyboard.Keys.HOME);
      cy.get(systemTab)
        .should('be.focused')
        .and('have.attr', 'aria-selected', 'true');
      cy.get('[data-plan-body="planet-1"]').should('contain.text', '3/3');
      cy.get('[data-plan-body="planet-1"] .slot-source').should(
        'have.text',
        'predicted',
      );
      cy.press(Cypress.Keyboard.Keys.RIGHT);
      cy.get(orderTab)
        .should('be.focused')
        .and('have.attr', 'aria-selected', 'true');
      cy.get('[data-plan-build-order] [data-plan-site]').should(
        'have.length',
        3,
      );
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(0)
        .should('contain.text', 'Primary Port');
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(1)
        .should('contain.text', 'Mining Installation');
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(2)
        .should('contain.text', 'Industrial Installation')
        .and('contain.text', 'OK');

      cy.get('[aria-label="Planner breadcrumbs"]')
        .contains('a', 'Inspect')
        .click();
      cy.location('pathname').should('eq', '/inspect');
      cy.contains('h1', systemName).should('be.visible');
      cy.go('back');
      cy.location('pathname').should('eq', '/plan');
      cy.location('search').should('include', 'tab=build-order');
      cy.get(orderTab).should('have.attr', 'aria-selected', 'true');
      cy.get('[data-plan-build-order] [data-plan-site]')
        .should('have.length', 3)
        .eq(1)
        .should('contain.text', 'Mining Installation');

      cy.visit('/plan');
      cy.get('h1').should('have.text', systemName);
      cy.get(systemTab).should('have.attr', 'aria-selected', 'true');
      cy.get(orderTab).click();
      cy.get('[data-plan-build-order] [data-plan-site]').should(
        'have.length',
        3,
      );

      cy.window().then((window) => {
        cy.stub(window, 'confirm').as('confirmRaven').returns(true);
      });
      cy.contains('summary', /^Import$/).click();
      cy.contains('button', /^Import from Raven$/).click();
      cy.get(
        'input[aria-label="Import Raven Colonial layout file"]',
      ).selectFile('cypress/fixtures/plan/raven-layout.json', { force: true });
      cy.get('@confirmRaven').should('have.been.calledOnce');
      cy.get('[data-plan-build-order] [data-plan-site]').should(
        'have.length',
        2,
      );
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(0)
        .should('contain.text', 'Lantern Harbour')
        .and('contain.text', 'Primary Port');
      cy.get('[data-plan-build-order] [data-plan-site]')
        .eq(1)
        .should('contain.text', 'Copper Quarry')
        .and('contain.text', 'A A Belt')
        .and('not.contain.text', 'Cluster 3');
      cy.contains('summary', /^Import notes \(/).click();
      cy.contains('li', /Cluster 3/).should('contain.text', 'A A Belt');
      cy.get('[aria-live="polite"]').should('contain.text', '0 blocked');
      cy.injectAxe();
      cy.checkA11y();
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width);
      });
      cy.screenshot(`plan/raven-${Cypress.browser.name}-${width}x${height}`, {
        capture: 'fullPage',
        overwrite: true,
      });
    });
  }
});

describe(
  'Colony planner reduced motion',
  { browser: { family: 'chromium' } },
  () => {
    beforeEach(() => installApiFixtures(3_000));
    afterEach(() => {
      cy.then(() =>
        Cypress.automation('remote:debugger:protocol', {
          command: 'Emulation.setEmulatedMedia',
          params: { features: [] },
        }),
      );
    });

    it('keeps keyboard tabs, explicit Add and Review usable with motion disabled', () => {
      cy.then(() =>
        Cypress.automation('remote:debugger:protocol', {
          command: 'Emulation.setEmulatedMedia',
          params: {
            features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
          },
        }),
      );
      cy.visit(`/plan?system=${systemId}&tab=invalid`);
      cy.wait(['@catalogue', '@planBodies']);
      cy.window().should((window) => {
        expect(
          window.matchMedia('(prefers-reduced-motion: reduce)').matches,
        ).to.equal(true);
      });
      cy.get(systemTab).should('have.attr', 'aria-selected', 'true').focus();
      cy.press(Cypress.Keyboard.Keys.LEFT);
      cy.get(orderTab)
        .should('be.focused')
        .and('have.attr', 'aria-selected', 'true');
      cy.location('search').should('include', 'tab=build-order');
      cy.press(Cypress.Keyboard.Keys.RIGHT);
      cy.get(systemTab)
        .should('be.focused')
        .and('have.attr', 'aria-selected', 'true');
      cy.location('search').should('not.include', 'tab=');
      cy.get('[role="tablist"] [role="tab"]').each(($tab) => {
        const style = getComputedStyle($tab[0]);
        expect(style.animationName).to.equal('none');
        expect(style.transitionDuration).to.equal('0s');
      });
      addFacility(primaryId, 0);
      cy.get('[data-plan-summary]').should('have.attr', 'aria-busy', 'true');
      cy.injectAxe();
      cy.checkA11y();
      cy.screenshot(`plan/reduced-motion-${Cypress.browser.name}`, {
        capture: 'viewport',
        overwrite: true,
      });
      cy.contains('button', /^Review & export$/)
        .focus()
        .click();
      cy.get(reviewDialog).should(($dialog) => {
        const style = getComputedStyle($dialog[0]);
        expect(style.animationName).to.equal('none');
        expect(style.transitionDuration).to.equal('0s');
      });
      cy.checkA11y(reviewDialog);
      cy.get(
        `${reviewDialog} [data-review-kind="unknown-slot"][data-body-ref="star-a"][data-slot-kind="orbital"]`,
      ).click();
      cy.get(
        '[data-plan-body="star-a"] button[aria-label^="Orbital slots on"]',
      ).should('be.focused');
      cy.press(Cypress.Keyboard.Keys.ESC);
      cy.get(reviewDialog).should('not.exist');
      cy.contains('button', /^Review & export$/).should('be.focused');
    });
  },
);
