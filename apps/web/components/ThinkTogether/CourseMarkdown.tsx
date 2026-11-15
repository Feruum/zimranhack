'use client'
import { parseCourseBlocks, exerciseBinding } from '@services/thinktogether/course-blocks'
import { Markdown } from './Shared'
import LessonExercise from './LessonExercise'

const labels = { explain: 'Explanation', example: 'Example', reflection: 'Reflect', exercise: 'Practice' }

export default function CourseMarkdown({ children, lessonId }: { children: string; lessonId?: string }) {
  let blocks
  try { blocks = parseCourseBlocks(children) } catch {
    return <p role="alert" className="text-sm text-destructive">This chapter has an invalid block format. An expert must correct its Markdown tags.</p>
  }
  return <div className="space-y-5">{blocks.map((block, index) => {
    if (block.type === 'markdown') return <Markdown key={index}>{block.text}</Markdown>
    const binding = block.type === 'exercise' ? exerciseBinding(block.text) : null
    return <section key={block.id || index} className={`space-y-3 rounded-lg border p-4 ${block.type === 'example' ? 'bg-muted/40' : ''}`} aria-label={labels[block.type]}>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{labels[block.type]}</p>
      <Markdown>{binding?.text ?? block.text.replace(/\{\{csv:[a-z0-9_]+\}\}/g, '')}</Markdown>
      {block.type === 'exercise' && (binding && lessonId ? <LessonExercise lessonId={lessonId} itemId={binding.itemId} /> : <p className="text-sm text-muted-foreground">Exercise preparation in progress.</p>)}
    </section>
  })}</div>
}
