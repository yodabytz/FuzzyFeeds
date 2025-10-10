#!/usr/bin/env python3
"""
MMA Story Generator - Fetches new MMA stories, rewrites them, adds images, and creates full content
"""

import feedparser
import requests
import json
import logging
import time
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, quote
from typing import Dict, List, Optional, Tuple
import sys
import os

# Add image enhancement system
sys.path.append('/home/snoopy/NewFuzzyFeeds')
from image_enhancement import find_mma_image

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class MMAStoryGenerator:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # MMA RSS feeds to monitor
        self.mma_feeds = [
            'https://www.mmafighting.com/rss/index.xml',
            'https://cagesidepress.com/feed/',
            'https://www.sherdog.com/rss.php',
            'https://www.mmajunkie.usatoday.com/rss',
            'https://www.bloodyelbow.com/rss/index.xml'
        ]
        
        # Story templates for rewriting
        self.story_templates = {
            'fight_result': {
                'intro': 'In an exciting display of mixed martial arts skill, ',
                'middle': 'The contest showcased the elite level of competition in modern MMA, with both athletes demonstrating exceptional technique and conditioning.',
                'analysis': 'This result significantly impacts the division rankings and sets up interesting matchmaking possibilities for future events.',
                'conclusion': 'Performances like this continue to elevate the sport and provide fans with the high-quality entertainment they expect from professional MMA.'
            },
            'announcement': {
                'intro': 'The MMA world received significant news today as ',
                'middle': 'This development represents an important milestone in the ongoing evolution of mixed martial arts.',
                'analysis': 'Industry analysts suggest this could have lasting implications for fighters, promotions, and fans alike.',
                'conclusion': 'Such announcements continue to shape the landscape of professional combat sports.'
            },
            'general': {
                'intro': 'Recent developments in the MMA community have brought attention to ',
                'middle': 'The mixed martial arts industry continues to evolve with new challenges and opportunities emerging regularly.',
                'analysis': 'These developments reflect the dynamic nature of professional combat sports and the business surrounding them.',
                'conclusion': 'As the sport continues to grow, these types of stories demonstrate the ongoing interest and investment in MMA.'
            }
        }
    
    def fetch_latest_stories(self, max_stories: int = 10) -> List[Dict]:
        """Fetch the latest MMA stories from RSS feeds."""
        all_stories = []
        
        for feed_url in self.mma_feeds:
            try:
                logging.info(f"Fetching from: {feed_url}")
                feed = feedparser.parse(feed_url)
                
                for entry in feed.entries[:3]:  # Get top 3 from each feed
                    story = {
                        'original_title': entry.title,
                        'original_link': entry.link,
                        'published': entry.published if hasattr(entry, 'published') else str(datetime.now()),
                        'source_domain': urlparse(feed_url).netloc,
                        'description': getattr(entry, 'description', ''),
                        'content': self._extract_content(entry)
                    }
                    all_stories.append(story)
                    
                time.sleep(2)  # Rate limiting
                
            except Exception as e:
                logging.error(f"Error fetching from {feed_url}: {e}")
                continue
        
        # Sort by publication date (newest first)
        all_stories.sort(key=lambda x: x['published'], reverse=True)
        return all_stories[:max_stories]
    
    def _extract_content(self, entry) -> str:
        """Extract content from RSS entry."""
        content = ''
        if hasattr(entry, 'content'):
            content = entry.content[0].value if entry.content else ''
        elif hasattr(entry, 'summary'):
            content = entry.summary
        elif hasattr(entry, 'description'):
            content = entry.description
        
        # Clean HTML tags
        content = re.sub(r'<[^>]+>', '', content)
        return content[:500] + '...' if len(content) > 500 else content
    
    def rewrite_story(self, original_story: Dict) -> Dict:
        """Rewrite an original story with new content and SEO optimization."""
        try:
            # Determine story type
            story_type = self._determine_story_type(original_story['original_title'])
            template = self.story_templates.get(story_type, self.story_templates['general'])
            
            # Extract key information
            fighters = self._extract_fighters(original_story['original_title'])
            organization = self._extract_organization(original_story['original_title'])
            event_info = self._extract_event_info(original_story['original_title'])
            
            # Generate new title
            new_title = self._generate_seo_title(original_story['original_title'], fighters, organization)
            
            # Generate slug
            slug = self._generate_slug(new_title)
            
            # Generate full content
            full_content = self._generate_full_content(original_story, template, fighters, organization)
            
            # Generate meta description
            meta_description = self._generate_meta_description(new_title, fighters, organization)
            
            # Generate tags
            tags = self._generate_tags(fighters, organization, story_type)
            
            # Find appropriate image
            image_url = find_mma_image(new_title)
            if not image_url:
                image_url = f"/images/news/2025/{slug[:20]}.jpg"  # Fallback
            
            rewritten_story = {
                'title': new_title,
                'slug': slug,
                'description': meta_description,
                'content': full_content,
                'image': image_url,
                'publishedTime': datetime.now(timezone.utc).isoformat(),
                'category': organization or 'MMA',
                'tags': tags,
                'author': 'FightPulse Editorial Team',
                'source_link': original_story['original_link'],
                'fighters': fighters,
                'organization': organization,
                'story_type': story_type
            }
            
            logging.info(f"Rewritten story: {new_title}")
            return rewritten_story
            
        except Exception as e:
            logging.error(f"Error rewriting story: {e}")
            return None
    
    def _determine_story_type(self, title: str) -> str:
        """Determine the type of MMA story."""
        title_lower = title.lower()
        
        fight_keywords = ['defeats', 'beats', 'submits', 'knockout', 'ko', 'tko', 'decision', 'wins', 'victory']
        if any(keyword in title_lower for keyword in fight_keywords):
            return 'fight_result'
        
        announcement_keywords = ['announces', 'signs', 'contract', 'retires', 'returns', 'suspended', 'released']
        if any(keyword in title_lower for keyword in announcement_keywords):
            return 'announcement'
        
        return 'general'
    
    def _extract_fighters(self, title: str) -> List[str]:
        """Extract fighter names from title."""
        # Common patterns for fighter names
        patterns = [
            r'(\w+\s+\w+)\s+(?:defeats|beats|submits|knocks out)\s+(\w+\s+\w+)',
            r'(\w+\s+\w+)\s+(?:vs\.?|v\.?)\s+(\w+\s+\w+)',
            r'(\w+\s+\w+)\s+(?:and|,)\s+(\w+\s+\w+)',
        ]
        
        fighters = []
        for pattern in patterns:
            matches = re.findall(pattern, title, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    fighters.extend([name.strip() for name in match if len(name.strip()) > 3])
        
        return list(set(fighters))  # Remove duplicates
    
    def _extract_organization(self, title: str) -> Optional[str]:
        """Extract MMA organization from title."""
        orgs = ['UFC', 'Bellator', 'PFL', 'ONE Championship', 'BKFC']
        title_upper = title.upper()
        
        for org in orgs:
            if org.upper() in title_upper:
                return org
        return 'UFC'  # Default
    
    def _extract_event_info(self, title: str) -> Dict:
        """Extract event information from title."""
        event_info = {}
        
        # Extract event numbers (e.g., UFC 309)
        ufc_match = re.search(r'UFC\s+(\d+)', title, re.IGNORECASE)
        if ufc_match:
            event_info['event_number'] = ufc_match.group(1)
            event_info['full_event'] = f"UFC {ufc_match.group(1)}"
        
        # Extract locations
        location_patterns = [r'in\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', r'at\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)']
        for pattern in location_patterns:
            location_match = re.search(pattern, title)
            if location_match:
                event_info['location'] = location_match.group(1)
                break
        
        return event_info
    
    def _generate_seo_title(self, original_title: str, fighters: List[str], organization: str) -> str:
        """Generate SEO-optimized title."""
        # Keep the essence but make it unique and SEO-friendly
        base_title = original_title
        
        # Add organization if not present
        if organization and organization not in base_title:
            base_title = f"{base_title} - {organization}"
        
        # Ensure it's under 60 characters for SEO
        if len(base_title) > 60:
            base_title = base_title[:57] + "..."
        
        return base_title
    
    def _generate_slug(self, title: str) -> str:
        """Generate URL slug from title."""
        # Convert to lowercase and replace spaces with hyphens
        slug = re.sub(r'[^\w\s-]', '', title.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        return slug[:50]  # Limit length
    
    def _generate_full_content(self, original_story: Dict, template: Dict, fighters: List[str], organization: str) -> str:
        """Generate full article content."""
        content_parts = []
        
        # Introduction paragraph
        intro = template['intro'] + original_story['description'][:200] + "."
        content_parts.append(f"<p>{intro}</p>")
        
        # Main content based on story type
        if fighters:
            fighter_info = f"<p>The bout featured {' and '.join(fighters[:2])}, both elite athletes in the {organization or 'MMA'} roster.</p>"
            content_parts.append(fighter_info)
        
        # Middle section
        content_parts.append(f"<p>{template['middle']}</p>")
        
        # Analysis section
        if organization:
            analysis = f"<p>This development in {organization} represents a significant moment for the promotion and its athletes. {template['analysis']}</p>"
            content_parts.append(analysis)
        
        # Conclusion
        content_parts.append(f"<p>{template['conclusion']}</p>")
        
        return "\n".join(content_parts)
    
    def _generate_meta_description(self, title: str, fighters: List[str], organization: str) -> str:
        """Generate SEO meta description."""
        base_desc = title
        if fighters:
            base_desc += f" featuring {', '.join(fighters[:2])}"
        if organization:
            base_desc += f" in {organization}"
        
        # Limit to 155 characters for SEO
        if len(base_desc) > 155:
            base_desc = base_desc[:152] + "..."
        
        return base_desc
    
    def _generate_tags(self, fighters: List[str], organization: str, story_type: str) -> List[str]:
        """Generate relevant tags for the story."""
        tags = []
        
        if organization:
            tags.append(organization)
        
        if fighters:
            tags.extend(fighters[:3])  # Add up to 3 fighter names
        
        # Add story type tags
        if story_type == 'fight_result':
            tags.extend(['Fight Results', 'MMA'])
        elif story_type == 'announcement':
            tags.extend(['MMA News', 'Announcement'])
        else:
            tags.append('MMA News')
        
        return tags[:8]  # Limit to 8 tags
    
    def save_story_to_website(self, story: Dict) -> bool:
        """Save the generated story to the website structure."""
        try:
            # Create the story file content
            astro_content = self._generate_astro_file_content(story)
            
            # Save to the appropriate location
            story_path = f"/var/www/testing.fightpulse.net/src/pages/news/{story['slug']}.astro"
            
            with open(story_path, 'w', encoding='utf-8') as f:
                f.write(astro_content)
            
            logging.info(f"Saved story to: {story_path}")
            
            # Also update the news index to include this story
            self._update_news_index(story)
            
            return True
            
        except Exception as e:
            logging.error(f"Error saving story: {e}")
            return False
    
    def _generate_astro_file_content(self, story: Dict) -> str:
        """Generate Astro file content for the story."""
        return f'''---
import Layout from '../../layouts/Layout.astro';
import ShareBar from '../../components/ShareBar.astro';
import ViewsCounter from '../../components/ViewsCounter.astro';

const article = {{
  title: "{story['title']}",
  description: "{story['description']}",
  image: "{story['image']}",
  publishedTime: "{story['publishedTime']}",
  category: "{story['category']}",
  tags: {json.dumps(story['tags'])},
  slug: "{story['slug']}",
  author: "{story['author']}",
  fighters: {json.dumps(story['fighters'])},
  organization: "{story['organization']}"
}};

const currentUrl = `https://testing.fightpulse.net/news/${{article.slug}}`;
const absoluteImageUrl = article.image.startsWith('http') 
  ? article.image 
  : `https://testing.fightpulse.net${{article.image}}`;
---

<Layout 
  title={{`${{article.title}} | FightPulse`}}
  description={{article.description}}
  image={{absoluteImageUrl}}
  canonical={{currentUrl}}
  article={{true}}
  publishedTime={{article.publishedTime}}
  tags={{article.tags}}
  category={{article.category}}
>
  <!-- Open Graph Meta Tags -->
  <meta property="og:title" content={{article.title}} />
  <meta property="og:description" content={{article.description}} />
  <meta property="og:image" content={{absoluteImageUrl}} />
  <meta property="og:url" content={{currentUrl}} />
  <meta property="og:type" content="article" />
  <meta property="og:site_name" content="FightPulse" />
  <meta property="article:author" content={{article.author}} />
  <meta property="article:published_time" content={{article.publishedTime}} />
  <meta property="article:section" content={{article.category}} />
  {{article.tags.map(tag => (
    <meta property="article:tag" content={{tag}} />
  ))}}
  
  <!-- Twitter Card Meta Tags -->
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content={{article.title}} />
  <meta name="twitter:description" content={{article.description}} />
  <meta name="twitter:image" content={{absoluteImageUrl}} />
  
  <!-- Additional SEO Meta Tags -->
  <meta name="author" content={{article.author}} />
  <meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large" />
  <link rel="canonical" href={{currentUrl}} />

  <div class="article-container">
    <div class="container">
      <article class="article-content">
        <header class="article-header">
          <div class="article-meta">
            <span class="article-category">{{article.category}}</span>
            <time class="article-date" datetime={{article.publishedTime}}>
              {{new Date(article.publishedTime).toLocaleDateString('en-US', {{ 
                year: 'numeric', 
                month: 'long', 
                day: 'numeric' 
              }})}}
            </time>
          </div>
          <h1 class="article-title">{{article.title}}</h1>
          <p class="article-description">{{article.description}}</p>
        </header>

        <div class="article-hero">
          <img src={{article.image}} alt={{article.title}} class="article-image" loading="eager">
        </div>

        <div class="article-body">
          {story['content']}
          
          {{article.fighters.length > 0 && (
            <div class="fighters-info">
              <h3>Featured Fighters</h3>
              <ul>
                {{article.fighters.map(fighter => (
                  <li>{{fighter}}</li>
                ))}}
              </ul>
            </div>
          )}}
          
          <div class="article-footer">
            <p><strong>Organization:</strong> {{article.organization}}</p>
            <p><strong>Category:</strong> {{article.category}}</p>
            <div class="article-tags">
              {{article.tags.map(tag => (
                <span class="tag">{{tag}}</span>
              ))}}
            </div>
          </div>
        </div>

        <div class="article-actions">
          <ViewsCounter slug={{article.slug}} />
          <ShareBar 
            title={{article.title}}
            description={{article.description}}
          />
        </div>
      </article>
    </div>
  </div>
</Layout>

<style>
  .article-container {{
    padding: var(--fp-space-8) 0;
    min-height: 100vh;
  }}

  .article-content {{
    max-width: 800px;
    margin: 0 auto;
  }}

  .article-header {{
    text-align: center;
    margin-bottom: var(--fp-space-8);
  }}

  .article-meta {{
    display: flex;
    justify-content: center;
    gap: var(--fp-space-4);
    margin-bottom: var(--fp-space-4);
    font-size: var(--fp-text-sm);
  }}

  .article-category {{
    background: var(--fp-primary);
    color: white;
    padding: 4px 12px;
    border-radius: var(--fp-radius);
    font-weight: var(--fp-font-semibold);
  }}

  .article-date {{
    color: var(--fp-text-secondary);
  }}

  .article-title {{
    font-size: var(--fp-text-4xl);
    font-weight: var(--fp-font-bold);
    color: var(--fp-text-primary);
    margin-bottom: var(--fp-space-4);
    line-height: 1.2;
  }}

  .article-description {{
    font-size: var(--fp-text-lg);
    color: var(--fp-text-secondary);
    line-height: 1.6;
  }}

  .article-hero {{
    margin: var(--fp-space-8) 0 var(--fp-space-12) 0;
    padding: 0 var(--fp-space-4);
  }}

  .article-image {{
    width: 100%;
    max-height: 500px;
    object-fit: cover;
    border-radius: var(--fp-radius-lg);
    box-shadow: var(--fp-shadow-lg);
  }}

  .article-body {{
    line-height: 1.7;
    color: var(--fp-text-primary);
  }}

  .article-body p {{
    margin-bottom: var(--fp-space-4);
    font-size: var(--fp-text-lg);
  }}

  .fighters-info {{
    background: var(--fp-bg-secondary);
    padding: var(--fp-space-6);
    border-radius: var(--fp-radius);
    margin: var(--fp-space-8) 0;
  }}

  .fighters-info h3 {{
    color: var(--fp-primary);
    margin-bottom: var(--fp-space-3);
  }}

  .fighters-info ul {{
    list-style: none;
    padding: 0;
  }}

  .fighters-info li {{
    background: var(--fp-white);
    padding: var(--fp-space-2) var(--fp-space-4);
    margin-bottom: var(--fp-space-2);
    border-radius: var(--fp-radius);
    font-weight: var(--fp-font-semibold);
  }}

  .article-footer {{
    border-top: 1px solid var(--fp-border);
    padding-top: var(--fp-space-6);
    margin-top: var(--fp-space-8);
  }}

  .article-tags {{
    display: flex;
    flex-wrap: wrap;
    gap: var(--fp-space-2);
    margin-top: var(--fp-space-4);
  }}

  .tag {{
    background: var(--fp-bg-secondary);
    color: var(--fp-text-primary);
    padding: 4px 8px;
    border-radius: var(--fp-radius);
    font-size: var(--fp-text-sm);
    font-weight: var(--fp-font-medium);
  }}

  .article-actions {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--fp-space-4);
    margin-top: var(--fp-space-8);
    padding-top: var(--fp-space-6);
    border-top: 1px solid var(--fp-border);
  }}

  @media (max-width: 768px) {{
    .article-title {{
      font-size: var(--fp-text-3xl);
    }}

    .article-body p {{
      font-size: var(--fp-text-base);
    }}

    .article-actions {{
      flex-direction: column;
      align-items: stretch;
    }}
  }}
</style>
'''
    
    def _update_news_index(self, story: Dict) -> None:
        """Update the news index page to include the new story."""
        # This would update the news.astro file to include the new story
        # For now, just log that it should be updated
        logging.info(f"Story ready for news index: {story['title']}")
    
    def generate_new_stories(self, count: int = 3) -> List[Dict]:
        """Main method to generate new MMA stories."""
        logging.info(f"Starting generation of {count} new MMA stories...")
        
        # Fetch latest stories
        original_stories = self.fetch_latest_stories(count * 2)  # Get more than needed
        
        new_stories = []
        for original_story in original_stories[:count]:
            rewritten_story = self.rewrite_story(original_story)
            if rewritten_story:
                new_stories.append(rewritten_story)
        
        logging.info(f"Generated {len(new_stories)} new stories")
        return new_stories

def main():
    """Main function to run the story generator."""
    generator = MMAStoryGenerator()
    
    print("🚀 MMA STORY GENERATOR - FETCHING FRESH CONTENT")
    print("=" * 60)
    
    # Generate new stories
    new_stories = generator.generate_new_stories(3)
    
    print(f"\\n📰 GENERATED {len(new_stories)} NEW STORIES:")
    print("-" * 60)
    
    for i, story in enumerate(new_stories, 1):
        print(f"\\n{i}. {story['title']}")
        print(f"   Slug: {story['slug']}")
        print(f"   Category: {story['category']}")
        print(f"   Tags: {', '.join(story['tags'][:3])}...")
        print(f"   Fighters: {', '.join(story['fighters']) if story['fighters'] else 'N/A'}")
        print(f"   Image: {story['image']}")
        
        # Save story to website
        if generator.save_story_to_website(story):
            print(f"   ✅ Saved to website")
        else:
            print(f"   ❌ Failed to save")
    
    print(f"\\n✨ STORY GENERATION COMPLETE!")
    return new_stories

if __name__ == "__main__":
    main()