describe('ED-Finder V3 foundation', () => {
  for (const [width, height] of [
    [1280, 800],
    [1024, 768],
    [768, 1024],
    [390, 844],
  ] as const) {
    it(`keeps home and guest account usable at ${width}x${height}`, () => {
      cy.viewport(width, height);
      cy.intercept('GET', '/api/v1/auth/session').as('accountSession');
      cy.visit('/');
      cy.wait('@accountSession').its('response.statusCode').should('eq', 200);
      cy.contains('button', 'Sign in with Frontier').should('be.visible');
      cy.get('.workspace-header nav a').should(($links) => {
        expect(
          $links.toArray().map((link) => ({
            href: link.getAttribute('href'),
            label: link.textContent?.trim(),
          })),
        ).to.deep.equal([
          { href: '/explore', label: 'Explore' },
          { href: '/inspect', label: 'Inspect' },
          { href: '/plan', label: 'Plan' },
        ]);
      });
      cy.get('h1')
        .should('have.length', 1)
        .and('have.text', 'Find your next frontier.');
      cy.get('.journey-step').should('have.length', 3);
      cy.contains('.journey-step', 'Plan your colony').should(
        'have.attr',
        'href',
        '/plan',
      );
      cy.get('.home-hero img')
        .should('have.attr', 'alt', '')
        .should('have.attr', 'aria-hidden', 'true')
        .should(($image) => {
          const image = $image[0] as HTMLImageElement;
          expect(image.currentSrc).to.include(
            width <= 640
              ? 'home-hero-black-hole-960.webp'
              : 'home-hero-black-hole-1920.webp',
          );
        });
      cy.get('form[aria-label="Find a system"]')
        .should(($form) => {
          expect(($form[0] as HTMLFormElement).method).to.equal('get');
        })
        .within(() => {
          cy.contains('label', 'Reference system').should('be.visible');
          cy.get('[role="combobox"]').should('be.visible');
          cy.contains('button', 'Find a system').should('be.visible');
        });
      cy.contains('a', 'Or open Explore without a reference system').should(
        'have.attr',
        'href',
        '/explore',
      );
      cy.get('.home-hero input, .home-hero select, .home-hero button').should(
        ($controls) => {
          $controls.toArray().forEach((control) => {
            const bounds = control.getBoundingClientRect();
            expect(bounds.left).to.be.at.least(0);
            expect(bounds.right).to.be.at.most(width);
          });
        },
      );
      cy.get('.journey-step').should(($cards) => {
        const cards = $cards.toArray();
        const bounds = cards.map((card) => card.getBoundingClientRect());
        cards.forEach((card, index) => {
          expect(card.scrollWidth).to.be.at.most(card.clientWidth);
          expect(bounds[index].left).to.be.at.least(0);
          expect(bounds[index].right).to.be.at.most(width);
          if (index === 0) return;
          if (width > 640) {
            expect(bounds[index].top).to.equal(bounds[0].top);
            expect(bounds[index].height).to.equal(bounds[0].height);
            expect(bounds[index].left).to.be.at.least(bounds[index - 1].right);
          } else {
            expect(bounds[index].left).to.equal(bounds[0].left);
            expect(bounds[index].width).to.equal(bounds[0].width);
            expect(bounds[index].top).to.be.at.least(bounds[index - 1].bottom);
          }
        });
      });
      cy.contains(
        'footer',
        'ED-Finder was created using assets and imagery',
      ).should(($footer) => {
        const credit = $footer.text().replace(/\s+/g, ' ');
        expect(credit).to.contain(
          'with the permission of Frontier Developments plc',
        );
        expect(credit).to.contain(
          'no employee of Frontier Developments was involved',
        );
      });
      cy.get('.home-hero [role="combobox"]').focus();
      cy.press(Cypress.Keyboard.Keys.TAB);
      cy.get('.home-hero').then(($hero) => {
        if ($hero.find('select').length) {
          cy.get('.home-hero select').should('be.focused');
          cy.press(Cypress.Keyboard.Keys.TAB);
        }
      });
      cy.contains('button', 'Find a system').should('be.focused');
      cy.press(Cypress.Keyboard.Keys.TAB);
      cy.contains('a', 'Or open Explore without a reference system').should(
        'be.focused',
      );
      for (const tool of [
        'Find your system',
        'Understand its potential',
        'Plan your colony',
      ]) {
        cy.press(Cypress.Keyboard.Keys.TAB);
        cy.contains('.journey-step', tool).should('be.focused');
      }
      cy.injectAxe();
      cy.checkA11y();
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width);
      });
      cy.screenshot(`account/home-${Cypress.browser.name}-${width}x${height}`, {
        capture: 'fullPage',
        overwrite: true,
      });

      cy.contains('.journey-step', 'Plan your colony').click();
      cy.location('pathname').should('eq', '/plan');
      cy.contains('h1', 'Colony planner').should('be.visible');
      cy.get('.workspace-header nav a[aria-current="page"]')
        .should('have.length', 1)
        .and('have.attr', 'href', '/plan')
        .and('have.text', 'Plan');

      cy.visit('/account');
      cy.wait('@accountSession').its('response.statusCode').should('eq', 200);
      cy.contains('h2', 'Sign in to manage your account').should('be.visible');
      cy.contains('button', 'Sign in with Frontier')
        .focus()
        .should('be.focused');
      cy.injectAxe();
      cy.checkA11y();
      cy.document().should((document) => {
        expect(document.documentElement.scrollWidth).to.be.at.most(width);
      });
      cy.screenshot(
        `account/guest-${Cypress.browser.name}-${width}x${height}`,
        {
          capture: 'fullPage',
          overwrite: true,
        },
      );
      cy.reload();
      cy.wait('@accountSession').its('response.statusCode').should('eq', 200);
      cy.contains('h2', 'Sign in to manage your account').should('be.visible');
      cy.get('.workspace-header nav a')
        .contains('Explore')
        .focus()
        .should('be.focused');
      cy.press(Cypress.Keyboard.Keys.ENTER);
      cy.location('pathname').should('eq', '/explore');
      cy.get('h1').should('contain.text', 'Chart a promising system');
    });
  }

  it('loads the shell and exercises the real same-origin bootstrap', () => {
    cy.intercept('/api/health').as('health');
    cy.intercept('/api/v1/auth/session').as('session');
    cy.visit('/');
    cy.get('h1')
      .should('have.text', 'Find your next frontier.')
      .and('be.visible');
    cy.wait('@health').its('response.statusCode').should('eq', 200);
    cy.wait('@session').its('response.statusCode').should('eq', 200);
    cy.contains('dd', 'Connected').should('be.visible');
    cy.contains('dd', 'Guest').should('be.visible');
    cy.contains('button', 'Sign in with Frontier').should('be.visible');
  });

  it('proxies backend-owned routes to the disposable FastAPI service', () => {
    cy.request('/openapi.json?format=json').then(
      ({ body, headers, status }) => {
        expect(status).to.eq(200);
        expect(headers['content-type']).to.include('application/json');
        expect(body.paths).to.have.property('/api/health');
      },
    );

    cy.request({
      url: '/s/0?utm=x',
      followRedirect: false,
      failOnStatusCode: false,
    }).then(({ headers, status }) => {
      // FastAPI currently owns this valid numeric share route and redirects
      // browser user agents to canonical Inspect. A Svelte fallback would
      // instead return HTML 200.
      expect(status).to.eq(302);
      expect(headers.location).to.include('/inspect?system=0');
    });

    cy.request({ url: '/apiary', failOnStatusCode: false }).then(
      ({ headers, status }) => {
        // This near-prefix must remain on the Svelte side. Because it is not a
        // real application route, the correct frontend response is its HTML 404.
        expect(status).to.eq(404);
        expect(headers['content-type']).to.include('text/html');
      },
    );
  });

  it('supports direct navigation and refresh through the SPA fallback', () => {
    cy.visit('/explore');
    cy.get('h1')
      .should('contain.text', 'Chart a promising system')
      .and('be.visible');
    // The map is part of the page beside the Finder, not opened on demand.
    cy.get(
      '[data-explore-map-window] [role="status"][data-renderer-state="ready"]',
      {
        timeout: 20_000,
      },
    ).should('be.visible');
    cy.get('canvas[data-spatial-canvas]').should('have.length', 1);
    cy.reload();
    cy.get('h1')
      .should('contain.text', 'Chart a promising system')
      .and('be.visible');
    cy.get(
      '[data-explore-map-window] [role="status"][data-renderer-state="ready"]',
      {
        timeout: 20_000,
      },
    ).should('be.visible');
    cy.get('canvas[data-spatial-canvas]').should('have.length', 1);
  });

  it('rejects unknown journey routes instead of rendering a placeholder', () => {
    cy.visit('/explroe', { failOnStatusCode: false });
    cy.contains('Chart a promising system').should('not.exist');
    cy.contains(/not found/i).should('be.visible');
  });
});

describe('Home reduced motion', { browser: { family: 'chromium' } }, () => {
  afterEach(() => {
    cy.then(() =>
      Cypress.automation('remote:debugger:protocol', {
        command: 'Emulation.setEmulatedMedia',
        params: { features: [] },
      }),
    );
  });

  it('keeps home controls and journey steps still when keyboard focused', () => {
    cy.then(() =>
      Cypress.automation('remote:debugger:protocol', {
        command: 'Emulation.setEmulatedMedia',
        params: {
          features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
        },
      }),
    );
    cy.visit('/');
    cy.window().should((window) => {
      expect(
        window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      ).to.equal(true);
    });
    cy.get(
      '.home-hero input, .home-hero select, .home-hero button, .home-hero a, .journey-step',
    ).each(($action) => {
      cy.wrap($action)
        .focus()
        .should('be.focused')
        .should(($focused) => {
          const style = getComputedStyle($focused[0]);
          expect(style.animationName).to.equal('none');
          expect(style.transitionDuration).to.equal('0s');
        });
    });
    cy.injectAxe();
    cy.checkA11y();
  });
});
