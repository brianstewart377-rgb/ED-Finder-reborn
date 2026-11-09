import { describe, expect, it } from 'vitest';
import { compactPlanFixture } from './__fixtures__/compact-plan';
import { buildBodyTree, flattenBodyTree } from './locations';
import { buildSchematic } from './schematic';
import type { PlannerSite } from './model';

function setup(bodyCount = 5) {
  const fixture = compactPlanFixture(bodyCount, 0);
  const tree = () => flattenBodyTree(buildBodyTree(fixture.bodies));
  return { ...fixture, tree };
}

function site(
  id: string,
  bodyRef: string | null,
  status: PlannerSite['status'] = 'planned',
  facilityId = 'installation',
): PlannerSite {
  return {
    schema: 'cpe_site_v1',
    site_id: id,
    name: '',
    facility_id: facilityId,
    body_ref: bodyRef,
    slot_kind: 'orbital',
    status,
  };
}

describe('buildSchematic', () => {
  it('draws the star, its direct children in recorded order, then the belts', () => {
    const { tree, facilities } = setup(4);
    const items = buildSchematic(tree(), [], facilities);
    expect(items.map((item) => [item.kind, item.body_ref])).toEqual([
      ['star', '1'],
      ['body', '2'],
      ['body', '3'],
      ['body', '4'],
      ['belt', 'belt:1'],
      ['belt', 'belt:2'],
    ]);
    for (const item of items) {
      expect(item.marker).toBeNull();
      expect(item.builds).toBe(0);
      expect(item.primary).toBe(false);
    }
  });

  it('folds moons into the body they orbit and counts them', () => {
    const { bodies, tree, facilities } = setup(5);
    bodies.bodies[3].direct_parent_body_id = '2'; // body 4 orbits body 2
    bodies.bodies[4].direct_parent_body_id = '4'; // body 5 orbits body 4
    const items = buildSchematic(tree(), [], facilities);
    expect(items.map((item) => item.body_ref)).not.toContain('4');
    expect(items.map((item) => item.body_ref)).not.toContain('5');
    expect(items.find((item) => item.body_ref === '2')?.moons).toBe(2);
    expect(items.find((item) => item.body_ref === '3')?.moons).toBe(0);
  });

  it('marks the Primary Port, a single build by facility name, and several builds by count', () => {
    const { tree, facilities } = setup(4);
    const sites = [
      site('a', '2', 'planned', 'primary-port'),
      site('b', '3'),
      site('c', '4'),
      site('d', '4', 'building'),
    ];
    const items = buildSchematic(tree(), sites, facilities);
    const byRef = new Map(items.map((item) => [item.body_ref, item]));
    expect(byRef.get('2')).toMatchObject({
      builds: 1,
      marker: 'Primary Port',
      primary: true,
    });
    expect(byRef.get('3')).toMatchObject({
      builds: 1,
      marker: 'Research Installation',
      primary: false,
    });
    expect(byRef.get('4')).toMatchObject({ builds: 2, marker: '2 builds' });
    expect(byRef.get('1')?.marker).toBeNull();
  });

  it('says when the Primary Port shares its body with other builds', () => {
    const { tree, facilities } = setup(3);
    const sites = [site('a', '2', 'planned', 'primary-port'), site('b', '2')];
    const item = buildSchematic(tree(), sites, facilities).find(
      (candidate) => candidate.body_ref === '2',
    );
    expect(item).toMatchObject({
      builds: 2,
      marker: 'Primary Port + 1 more',
      primary: true,
    });
  });

  it('marks a build on a moon at the planet it orbits', () => {
    const { bodies, tree, facilities } = setup(4);
    bodies.bodies[3].direct_parent_body_id = '2';
    const items = buildSchematic(tree(), [site('a', '4')], facilities);
    expect(items.find((item) => item.body_ref === '2')?.builds).toBe(1);
  });

  it('leaves out demolished, unplaced and no-longer-current builds', () => {
    const { tree, facilities } = setup(3);
    const sites = [
      site('a', '2', 'demolished'),
      site('b', null),
      site('c', 'gone-body'),
    ];
    const items = buildSchematic(tree(), sites, facilities);
    expect(items.every((item) => item.builds === 0 && !item.marker)).toBe(true);
  });

  it('survives a parent loop without hanging', () => {
    const { bodies, tree, facilities } = setup(4);
    bodies.bodies[2].direct_parent_body_id = '4';
    bodies.bodies[3].direct_parent_body_id = '3';
    const items = buildSchematic(tree(), [site('a', '3')], facilities);
    expect(items.length).toBeGreaterThan(0);
  });
});
