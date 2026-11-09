// Normal application + real PostgreSQL transactions. Only the upstream
// provider identity is seeded by a guarded CI helper; no API responses are stubbed.
interface AccountFixture {
  cookie_name: string;
  sessions: Record<string, string>;
}

const journal = (fid: string) =>
  [
    {
      event: 'Fileheader',
      timestamp: '2026-01-10T00:00:00Z',
      gameversion: '4.1.0.0',
    },
    {
      event: 'Commander',
      timestamp: '2026-01-10T00:00:01Z',
      FID: fid,
      Name: 'Browser journal commander',
    },
    {
      event: 'LoadGame',
      timestamp: '2026-01-10T00:00:02Z',
      FID: fid,
      Commander: 'Browser journal commander',
      GameVersion: '4.1.0.0',
    },
    {
      event: 'Location',
      timestamp: '2026-01-10T00:00:03Z',
      StarSystem: 'Browser fixture',
      SystemAddress: 123,
    },
    {
      event: 'Scan',
      timestamp: '2026-01-10T00:00:04Z',
      SystemAddress: 123,
      BodyID: 1,
      BodyName: 'Browser fixture A',
      PlanetClass: 'Earthlike body',
      Radius: 3000000,
      SurfaceGravity: 9.80665,
    },
  ]
    .map((row) => JSON.stringify(row))
    .join('\n');

describe('Verified account and journal product acceptance', () => {
  for (const [width, height] of [
    [1280, 800],
    [390, 844],
  ] as const) {
    it(`imports mixed selections privately, retries, shares explicitly and logs out at ${width}px`, () => {
      const fixturePath = Cypress.env('accountFixtureFile');
      expect(typeof fixturePath, 'guarded disposable account fixture').to.eq(
        'string',
      );
      expect(fixturePath.length).to.be.greaterThan(0);
      cy.viewport(width, height);
      cy.readFile<AccountFixture>(fixturePath, { log: false }).then(
        (fixture) => {
          cy.setCookie(fixture.cookie_name, fixture.sessions[String(width)], {
            log: false,
          });
        },
      );
      cy.intercept('GET', '/api/v1/auth/commanders').as('commanders');
      cy.intercept('GET', '/api/v1/journal/galaxy-impact').as('impact');
      cy.intercept('POST', '/api/v1/journal/verified-imports').as('import');
      cy.intercept('POST', '/api/v1/journal/galaxy-contributions/imports/*').as(
        'offer',
      );
      cy.intercept('POST', '/api/v1/auth/logout').as('logout');
      cy.visit('/account');
      cy.wait('@commanders').its('response.statusCode').should('eq', 200);
      cy.wait('@impact').its('response.statusCode').should('eq', 200);
      cy.contains('h2', 'Your Galaxy Impact').should('be.visible');
      cy.contains(`F991${width}`).should('be.visible');
      cy.contains('button', 'Unlink').should('be.disabled');
      cy.get('input[type="checkbox"]').should('not.be.checked');
      cy.get('.commander-link').should('have.attr', 'aria-current', 'page');
      cy.injectAxe();
      cy.checkA11y();
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width);
      });
      cy.screenshot(
        `account/signed-in-${Cypress.browser.name}-${width}x${height}`,
        { capture: 'fullPage', overwrite: true },
      );

      // Guards the reproduced prod symptom: a build that ships the Import
      // button without the file picker (stale build predating #journal-files)
      // must fail here rather than silently rendering an unusable panel.
      cy.get('label[for="journal-files"]')
        .should('be.visible')
        .and('contain.text', 'Select journal logs');
      cy.get('#journal-files').should('exist');

      cy.get('#journal-files').selectFile(
        [
          {
            contents: Cypress.Buffer.from(journal(`F991${width}`)),
            fileName: 'mine.log',
          },
          {
            contents: Cypress.Buffer.from(journal('F9929999')),
            fileName: 'unlinked.log',
          },
          {
            contents: Cypress.Buffer.from(
              journal(`F991${width}`) +
                '\n' +
                JSON.stringify({
                  event: 'Commander',
                  FID: 'F9929999',
                  timestamp: 'invalid',
                }),
            ),
            fileName: 'malformed-identity.log',
          },
        ],
        { force: true },
      );
      cy.contains('button', 'Import journals').click();
      cy.wait('@import').then(({ request, response }) => {
        expect(
          request.body.files.map((file: { name: string }) => file.name),
        ).not.to.include('malformed-identity.log');
        expect(response?.statusCode).to.eq(200);
        expect(response?.body.events_inserted).to.be.greaterThan(0);
        expect(response?.body.held_files).to.deep.include({
          name: 'unlinked.log',
          reason: 'commander_not_linked',
        });
      });
      cy.contains('unlinked.log: commander not linked').should('be.visible');
      cy.contains(
        'malformed-identity.log: Commander identity record has an invalid timestamp',
      ).should('be.visible');
      cy.contains('dt', 'Bodies scanned').next('dd').should('have.text', '1');
      cy.contains('dt', 'Earth-Like Worlds')
        .next('dd')
        .should('have.text', '1');
      cy.contains('summary', 'Manage galaxy sharing').click();
      cy.contains('No observations shared on this page').should('be.visible');
      cy.get('@offer.all').should('have.length', 0);

      cy.get('input[type="checkbox"]').check();
      cy.contains('button', 'Import journals').should('be.enabled').click();
      cy.wait('@import').then(({ response }) => {
        expect(response?.statusCode).to.eq(200);
        expect(response?.body.events_inserted).to.eq(0);
        expect(response?.body.files_skipped).to.eq(1);
      });
      cy.wait('@offer').then(({ response }) => {
        expect(response?.statusCode).to.eq(200);
        expect(response?.body.new_offers).to.eq(1);
      });
      cy.contains('Awaiting review').should('be.visible');
      cy.contains('dt', 'Bodies scanned').next('dd').should('have.text', '1');
      cy.contains('button', 'Withdraw sharing').should('be.enabled').click();
      cy.contains('Sharing withdrawn').should('be.visible');
      cy.contains('button', 'Sign out').focus().should('be.focused').click();
      cy.wait('@logout').its('response.statusCode').should('eq', 200);
      cy.contains('h2', 'Sign in to manage your account').should('be.visible');
      cy.get('#journal-files').should('not.exist');
      cy.reload();
      cy.contains('h2', 'Sign in to manage your account').should('be.visible');
    });
  }

  it('explains why an all-held selection produced zero ready files', () => {
    const fixturePath = Cypress.env('accountFixtureFile');
    expect(typeof fixturePath, 'guarded disposable account fixture').to.eq(
      'string',
    );
    expect(fixturePath.length).to.be.greaterThan(0);
    cy.viewport(1280, 800);
    cy.readFile<AccountFixture>(fixturePath, { log: false }).then((fixture) => {
      // Dedicated session (not a width session another test consumes/rotates).
      cy.setCookie(fixture.cookie_name, fixture.sessions['held'], {
        log: false,
      });
    });
    cy.intercept('GET', '/api/v1/auth/commanders').as('commanders');
    cy.intercept('POST', '/api/v1/journal/verified-imports').as('import');
    cy.visit('/account');
    cy.wait('@commanders').its('response.statusCode').should('eq', 200);
    cy.get('#journal-files').should('exist');

    // Every selected file is held client-side (invalid Commander/LoadGame
    // timestamp), so no request is ever sent and the panel must explain why
    // instead of falling back to a bare "No files ready to import".
    cy.get('#journal-files').selectFile(
      [
        {
          contents: Cypress.Buffer.from(
            JSON.stringify({
              event: 'Commander',
              FID: 'F9911280',
              timestamp: 'invalid',
            }),
          ),
          fileName: 'no-valid-timestamp.log',
        },
      ],
      { force: true },
    );
    cy.contains('button', 'Import journals').should('be.enabled').click();
    cy.contains('[role="alert"]', 'All 1 selected file were held').should(
      'be.visible',
    );
    cy.contains('[role="alert"]', /Commander/).should('be.visible');
    cy.contains('No files ready to import').should('not.exist');
    cy.get('@import.all').should('have.length', 0);
  });
});
