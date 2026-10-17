export interface EditorialImage {
  file: string;
  alt: string;
  caption: string;
  creator: string;
  creatorUrl?: string;
  commonsUrl: string;
  license: string;
  licenseUrl: string;
}

export const editorialImages = {
  protest: {
    file: 'protest-berkeley-2023.jpg',
    alt: 'A crowd gathered at a pro-Palestinian protest at the University of California, Berkeley.',
    caption: 'Berkeley, California · 25 October 2023. Archive photograph; not a depiction of a current report.',
    creator: 'Kefr4000',
    creatorUrl: 'https://commons.wikimedia.org/wiki/User:Kefr4000',
    commonsUrl: 'https://commons.wikimedia.org/wiki/File:Crowd_at_Pro-Palestinian_Protest_2.jpg',
    license: 'CC0 1.0',
    licenseUrl: 'https://creativecommons.org/publicdomain/zero/1.0/',
  },
  conflict: {
    file: 'damaged-housing-stepanakert-2020.jpg',
    alt: 'A woman walks beside a damaged residential building in Stepanakert after fighting in October 2020.',
    caption: 'Stepanakert, Nagorno-Karabakh · 13 October 2020. Archive photograph; not a depiction of a current report.',
    creator: 'Yan Boechat / Voice of America',
    commonsUrl: 'https://commons.wikimedia.org/wiki/File:Destroyed_housing_compex_in_the_Republic_of_Artsakh_due_to_bombardment_from_Azerbaijan_in_September-October_2020.jpg',
    license: 'Public domain (Voice of America)',
    licenseUrl: 'https://commons.wikimedia.org/wiki/Template:PD-VOA',
  },
  humanitarian: {
    file: 'water-aid-kibati-2008.jpg',
    alt: 'A humanitarian water distribution point at Kibati camp in the Democratic Republic of the Congo.',
    caption: 'Kibati camp, Democratic Republic of the Congo · November 2008. Archive photograph; not a depiction of a current report.',
    creator: 'Julien Harneis',
    creatorUrl: 'https://commons.wikimedia.org/wiki/User:Julien_Harneis',
    commonsUrl: 'https://commons.wikimedia.org/wiki/File:Humanitarian_Aid_in_Congo_Water.jpeg',
    license: 'CC BY-SA 2.0',
    licenseUrl: 'https://creativecommons.org/licenses/by-sa/2.0/',
  },
  displacement: {
    file: 'darfur-refugee-camp-chad-2005.jpg',
    alt: 'Shelters at a camp for people displaced from Darfur in eastern Chad.',
    caption: 'Chad · 29 March 2005. Archive photograph; camp conditions and locations vary.',
    creator: 'Mark Knobil',
    creatorUrl: 'https://commons.wikimedia.org/wiki/User:Mark_Knobil',
    commonsUrl: 'https://commons.wikimedia.org/wiki/File:Darfur_refugee_camp_in_Chad.jpg',
    license: 'CC BY 2.0',
    licenseUrl: 'https://creativecommons.org/licenses/by/2.0/',
  },
  disaster: {
    file: 'nepal-earthquake-response-2015.jpg',
    alt: 'Search and rescue team members reach residents in earthquake-hit Chautara, Nepal.',
    caption: 'Chautara, Nepal · 29 April 2015. Archive photograph from the Nepal earthquake response.',
    creator: 'Jessica Lea / UK Department for International Development',
    commonsUrl: 'https://commons.wikimedia.org/wiki/File:Rescue_teams_reach_communities_in_earthquake-hit,_Chautara,_Nepal_(17126135830).jpg',
    license: 'CC BY 2.0',
    licenseUrl: 'https://creativecommons.org/licenses/by/2.0/',
  },
  diplomacy: {
    file: 'un-security-council-2023.jpg',
    alt: 'U.S. Secretary of State Antony Blinken chairs a high-level meeting of the UN Security Council.',
    caption: 'United Nations, New York · 3 August 2023. Archival photograph of a Council meeting.',
    creator: 'Chuck Kennedy / U.S. Department of State',
    commonsUrl: 'https://commons.wikimedia.org/wiki/File:Secretary_Blinken_Chairs_a_UN_Security_Council_High-Level_Meeting_-_53093635994.jpg',
    license: 'Public domain (U.S. Department of State)',
    licenseUrl: 'https://commons.wikimedia.org/wiki/Template:PD-USGov-DOS',
  },
  geography: {
    file: 'equal-earth-world-map.png',
    alt: 'A borderless Equal Earth projection world map centered on 150 degrees east.',
    caption: 'Equal Earth projection · a reference map, not live event data.',
    creator: 'Kingfiser',
    creatorUrl: 'https://commons.wikimedia.org/wiki/User:Kingfiser',
    commonsUrl: 'https://commons.wikimedia.org/wiki/File:BlankMap-World-150E_Equal_Earth.png',
    license: 'CC0 1.0',
    licenseUrl: 'https://creativecommons.org/publicdomain/zero/1.0/',
  },
} satisfies Record<string, EditorialImage>;

export type EditorialImageKey = keyof typeof editorialImages;
