import { redirect } from 'next/navigation';

export default function Home() {
  redirect('/workbench.html#overview');
}
